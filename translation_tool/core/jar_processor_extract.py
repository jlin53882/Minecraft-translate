"""translation_tool/core/jar_processor_extract.py 模組。

用途：從 Mod JAR 檔案中提取翻譯資源的功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。

重構記錄（PR #55）：
- 內部 JAR 讀取改用 `jar_browser.scan_jars()`，消除重複的 ZIP 掃描邏輯。
- 對 binary 檔案（UTF-8 decode 失敗）仍直接讀取 ZIP 以取得原始 bytes。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import zipfile
from collections.abc import Callable, Generator, Iterable
from pathlib import Path
from typing import Any

from translation_tool.utils.bounded_executor import bounded_as_completed
from translation_tool.utils.cancellation import (
    TaskCancelled,
    is_cancelled,
    raise_if_cancelled,
)
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor, run_in_context
from translation_tool.utils.worker_count import (
    configured_worker_value,
    resolve_worker_count,
)

from ..utils.config_manager import load_config
from ..utils.log_unit import log_error
from ..utils.nested_archive import read_archive_member_chain
from ..utils.zip_safety import (
    MAX_FILE_BYTES,
    MAX_TEXT_BYTES,
    ArchiveBudgetError,
    UnsafePathError,
    ZipReadBudget,
    ZipSizeError,
    read_limited,
    safe_join,
)

log = logging.getLogger(__name__)

# 背景預掃描的進度 yield 節流（秒）：避免 100+ JAR 的慢速掃描洗版日誌；測試可縮短。
YIELD_INTERVAL = 5.0

VERSION_REGEX = re.compile(
    r"[-_](?:[a-zA-Z]+-)?\d+(?:\.\d+)+(?:[-_.][a-zA-Z0-9]+)*$",
    re.IGNORECASE,
)
_EXTRACTED_WRAPPER_RE = re.compile(r"^.+_extracted(?:_\w+)?$", re.IGNORECASE)
_EXTRACTION_MANIFEST_NAME = "extraction_manifest"


def _is_safe_extracted_wrapper_name(wrapper: str) -> bool:
    """Return whether ``wrapper`` is a single generated directory name.

    The ownership manifest is generated locally, but it is still persisted
    metadata and may be truncated or edited independently of the extraction
    output.  Never let a manifest value turn the cleanup target into an
    absolute path or a path containing traversal components.
    """
    return (
        bool(_EXTRACTED_WRAPPER_RE.fullmatch(wrapper))
        and wrapper not in {".", ".."}
        and not any(separator in wrapper for separator in ("/", "\\", ":"))
        and Path(wrapper).name == wrapper
        and not Path(wrapper).is_absolute()
    )


def get_file_hash(data: bytes) -> str:
    """計算資料的 SHA-256 雜湊值（16進位字串）。

    Args:
        data: 原始位元組資料
    Returns:
        SHA-256 雜湊的 16 進位表示（64字元）
    """
    return hashlib.sha256(data).hexdigest()


def _normalize_jar_base_name(jar_filename: str) -> str:
    """從 JAR 檔名提取乾淨的 Mod ID（去除版本號、forge/fabric 等前輈字）。

    Args:
        jar_filename: JAR 檔案路徑或檔名
    Returns:
        乾淨的 Mod ID（如 "Botania"）
    """
    base_full = os.path.splitext(os.path.basename(jar_filename))[0]
    clean_name = re.sub(
        r"[-_](neoforge|forge|fabric|quilt|build|release|alpha|beta)[-_]?",
        "-",
        base_full,
        flags=re.IGNORECASE,
    ).strip("-_")
    match_version = VERSION_REGEX.search(clean_name)
    if match_version:
        base_name = clean_name[: match_version.start()].strip("-_")
    else:
        base_name = clean_name
    return base_name or base_full


def build_collision_safe_output_names(
    jar_paths: Iterable[str | Path],
    root: str | Path | None = None,
) -> dict[Path, str]:
    """為輸出 wrapper 可能碰撞的 outer JAR 建立穩定目錄名。

    一般情況保留既有的 ``<normalized-jar-name>_extracted`` 路徑；只有同一批
    來源中有相同 normalized JAR 名稱時，才把相對來源路徑的短雜湊插入 stem。
    這樣既保持既有輸出相容性，也避免不同 outer JAR 的 nested 或 ``data/``
    資源寫到同一個資料夾。命名保留 ``_extracted`` 尾碼，讓既有 Stage 2／
    指南掃描器仍能識別 wrapper。
    """
    unique_paths = list(dict.fromkeys(Path(path) for path in jar_paths))
    grouped: dict[str, list[Path]] = {}
    for path in unique_paths:
        grouped.setdefault(_normalize_jar_base_name(path.name).casefold(), []).append(
            path
        )

    if root is not None:
        root_path = Path(root).resolve()
    else:
        try:
            root_path = Path(
                os.path.commonpath(
                    [str(path.resolve().parent) for path in unique_paths]
                )
            )
        except (OSError, ValueError):
            root_path = None

    output_names: dict[Path, str] = {}
    for paths in grouped.values():
        if len(paths) < 2:
            continue
        for path in paths:
            try:
                if root_path is None:
                    raise ValueError("no common source root")
                identity = path.resolve().relative_to(root_path).as_posix()
            except (OSError, ValueError):
                identity = path.resolve().as_posix()
            digest = hashlib.blake2s(
                identity.casefold().encode("utf-8"), digest_size=4
            ).hexdigest()
            output_names[path] = f"{path.stem}_{digest}_extracted"
    return output_names


def build_nested_output_names(
    jar_paths: Iterable[str | Path],
    root: str | Path | None = None,
) -> dict[Path, str]:
    """Backward-compatible alias for collision-safe extracted wrapper names."""
    return build_collision_safe_output_names(jar_paths, root)


def _source_manifest_entry(path: Path, wrapper: str) -> dict[str, Any]:
    """Return the durable source identity used to own one extracted wrapper."""
    resolved = path.resolve()
    try:
        stat = resolved.stat()
    except OSError:
        # The extraction phase will report a missing JAR as an error.  Keep a
        # path-only identity here so cleanup remains conservative and does not
        # confuse a disappeared source with an unchanged source.
        size: int | None = None
        mtime_ns: int | None = None
    else:
        size = stat.st_size
        mtime_ns = stat.st_mtime_ns
    return {
        "path": str(resolved),
        "wrapper": wrapper,
        "size": size,
        "mtime_ns": mtime_ns,
    }


def _manifest_wrappers_for_jar(
    jar_path: Path,
    nested_output_name: str | None,
) -> tuple[str, ...]:
    """Return every generated wrapper a JAR can use in the current layout.

    Non-colliding JARs intentionally keep the historical distinction between
    direct non-``assets/`` output (normalized mod name) and nested output
    (full JAR stem).  A collision-safe name is shared by both paths.  The
    manifest must record both historical wrappers so stale nested output can
    be invalidated as well as direct output.
    """
    if nested_output_name is not None:
        return (nested_output_name,)
    wrappers = {
        f"{_normalize_jar_base_name(jar_path.name)}_extracted",
        f"{jar_path.stem}_extracted",
    }
    return tuple(sorted(wrappers, key=str.casefold))


def _load_extraction_manifest(
    path: Path,
) -> dict[str, set[tuple[str, int | None, int | None]]] | None:
    """Load a validated wrapper ownership manifest, or ``None`` for legacy output."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    sources = payload.get("sources")
    if not isinstance(sources, list):
        return None

    result: dict[str, set[tuple[str, int | None, int | None]]] = {}
    for item in sources:
        if not isinstance(item, dict):
            return None
        source_path = item.get("path")
        wrapper = item.get("wrapper")
        size = item.get("size")
        mtime_ns = item.get("mtime_ns")
        if (
            not isinstance(source_path, str)
            or not isinstance(wrapper, str)
            or not _is_safe_extracted_wrapper_name(wrapper)
            or (size is not None and not isinstance(size, int))
            or (mtime_ns is not None and not isinstance(mtime_ns, int))
        ):
            return None
        result.setdefault(wrapper, set()).add((source_path, size, mtime_ns))
    return result


def _write_extraction_manifest(path: Path, sources: list[dict[str, Any]]) -> None:
    """Publish the current wrapper ownership atomically before extraction starts."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            {"version": 1, "sources": sources},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _reconcile_extracted_wrappers(
    output_dir: str,
    jar_paths: Iterable[str | Path],
    nested_output_names: dict[Path, str],
) -> None:
    """Remove wrappers that no longer belong to the current extraction snapshot.

    Extraction output is an intermediate, generated tree.  A manifest lets us
    invalidate only wrappers whose source set or source stat changed, while a
    missing/invalid manifest gets a one-time legacy cleanup of top-level
    ``*_extracted`` directories.  This prevents a later Stage 1/Stage 2 pass
    from treating a removed JAR's old collision wrapper as a live source.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    # Keep metadata beside the generated tree.  FolderReader intentionally
    # enumerates every file under ``output_dir``; placing the manifest inside
    # it would make it look like a translatable ``other_output`` source.
    manifest_path = (
        output_path.parent / f".{output_path.name}.{_EXTRACTION_MANIFEST_NAME}"
    )
    current_sources: list[dict[str, Any]] = []
    current_by_wrapper: dict[str, set[tuple[str, int | None, int | None]]] = {}
    for jar_path in dict.fromkeys(Path(path) for path in jar_paths):
        for wrapper in _manifest_wrappers_for_jar(
            jar_path, nested_output_names.get(jar_path)
        ):
            entry = _source_manifest_entry(jar_path, wrapper)
            current_sources.append(entry)
            current_by_wrapper.setdefault(wrapper, set()).add(
                (entry["path"], entry["size"], entry["mtime_ns"])
            )

    previous_by_wrapper = _load_extraction_manifest(manifest_path)
    if previous_by_wrapper is not None:
        # Version 1 originally recorded only the normalized direct-output
        # wrapper.  Reconstruct the historical nested wrapper from the source
        # path so an upgrade can also remove stale versioned-JAR output.
        for wrapper, sources in tuple(previous_by_wrapper.items()):
            for source_path, size, mtime_ns in sources:
                legacy_nested_wrapper = f"{Path(source_path).stem}_extracted"
                if _is_safe_extracted_wrapper_name(legacy_nested_wrapper):
                    previous_by_wrapper.setdefault(legacy_nested_wrapper, set()).add(
                        (source_path, size, mtime_ns)
                    )
    if previous_by_wrapper is None:
        wrappers_to_remove = {
            entry.name
            for entry in output_path.iterdir()
            if entry.is_dir() and _EXTRACTED_WRAPPER_RE.fullmatch(entry.name)
        }
        if manifest_path.exists():
            log.warning(
                "[extract] 無法讀取 %s，將清理舊版 *_extracted 暫存 wrapper",
                manifest_path,
            )
    else:
        wrappers_to_remove = {
            wrapper
            for wrapper, sources in previous_by_wrapper.items()
            if current_by_wrapper.get(wrapper) != sources
        }
        # A wrapper created outside the manifest is not safe to merge into the
        # current snapshot.  It is generated staging data, so reset it before
        # allowing the current extraction to repopulate it.
        wrappers_to_remove.update(
            wrapper
            for wrapper in current_by_wrapper
            if wrapper not in previous_by_wrapper
        )

    for wrapper in sorted(wrappers_to_remove, key=str.casefold):
        target = output_path / wrapper
        if not target.exists():
            continue
        if target.is_symlink() or not target.is_dir():
            raise OSError(f"拒絕清理非目錄 extracted wrapper: {target}")
        shutil.rmtree(target)
        log.info("[extract] 清理失效 extracted wrapper: %s", target)

    _write_extraction_manifest(manifest_path, current_sources)


def _entry_output_path(
    output_root: str,
    jar_filename_base: str,
    jar_stem: str,
    logical_path: str,
    is_nested: bool,
    nested_output_name: str | None = None,
) -> str:
    """把 direct/nested archive member 映射成翻譯流程的 canonical 路徑。"""
    if is_nested:
        # Nested resources must retain the complete outer JAR identity.  The
        # normalized name intentionally collapses loader/version suffixes and
        # is therefore not unique in a large modpack (for example, many mods
        # normalize to ``ars`` or ``ftb``).  The same wrapper is also used for
        # direct ``data/`` resources when the normalized names collide.
        return safe_join(
            output_root,
            nested_output_name or f"{jar_stem}_extracted",
            logical_path,
        )
    if not logical_path.startswith("assets/"):
        return safe_join(
            output_root,
            nested_output_name or f"{jar_filename_base}_extracted",
            logical_path,
        )
    return safe_join(output_root, logical_path)


def _extract_scanned_entries(
    jar_path: Path,
    output_root: str,
    jar_filename_base: str,
    entries: list[Any],
    budget: ZipReadBudget,
    get_file_hash_fn: Callable[[bytes], str],
    asset_winners: dict[str, Path] | None = None,
    nested_output_name: str | None = None,
) -> dict[str, Any]:
    """寫出預掃描保留的 direct/nested entries，避免再掃一次 archive tree。"""
    extracted_count = 0
    skipped_count = 0
    for entry in entries:
        raise_if_cancelled()
        normalized_path = entry.logical_path
        if (
            asset_winners
            and not entry.is_nested
            and normalized_path.startswith("assets/")
            and asset_winners.get(normalized_path) not in (None, jar_path)
        ):
            # A shared assets path has one deterministic owner.  Count this
            # source as skipped instead of letting worker completion order pick
            # the final bytes.
            skipped_count += 1
            continue
        try:
            final_output_path = _entry_output_path(
                output_root,
                jar_filename_base,
                jar_path.stem,
                normalized_path,
                entry.is_nested,
                nested_output_name,
            )
        except UnsafePathError:
            log_error("拒絕提取不安全路徑: %s（來源 %s）", normalized_path, jar_path)
            skipped_count += 1
            continue

        try:
            if entry.content is not None:
                source_data = entry.content.encode("utf-8")
            elif entry.raw_data is not None:
                source_data = entry.raw_data
            else:
                source_data = read_archive_member_chain(
                    jar_path,
                    entry.member_chain,
                    budget,
                    max_bytes=MAX_FILE_BYTES,
                )
        except ArchiveBudgetError:
            raise
        except ZipSizeError as size_err:
            log_error("略過過大檔案 %s: %s", normalized_path, size_err)
            skipped_count += 1
            continue

        source_hash = get_file_hash_fn(source_data)
        raise_if_cancelled()
        if os.path.exists(final_output_path):
            with open(final_output_path, "rb") as existing_file:
                existing_hash = get_file_hash_fn(existing_file.read())
            if source_hash == existing_hash:
                skipped_count += 1
                continue

        os.makedirs(os.path.dirname(final_output_path), exist_ok=True)
        with open(final_output_path, "wb") as target:
            target.write(source_data)
        extracted_count += 1

    return {
        "status": "success",
        "extracted": extracted_count,
        "skipped": skipped_count,
    }


def _ensure_scan_results(
    scan_results: dict[Path, dict[str, str | None]] | None,
    jar_path: Path,
    target_regex: re.Pattern,
    budget: ZipReadBudget,
):
    """建立直接抽取所需的 canonical nested scan，保留既有預掃描結果。"""
    if scan_results is not None:
        return scan_results

    # Keep direct callers on the same bounded scanner as the normal pipeline.
    from translation_tool.utils.jar_browser import scan_jars

    return scan_jars(
        jar_path.parent,
        [target_regex],
        max_workers=1,
        jar_files=[jar_path],
        budget_factory=lambda _path: budget,
    )


def _extract_cached_entries_or_status(
    jar_path: Path,
    output_root: str,
    jar_filename_base: str,
    budget: ZipReadBudget,
    get_file_hash_fn: Callable[[bytes], str],
    scan_results,
    nested_output_name: str | None = None,
) -> dict[str, Any] | None:
    """處理 scan cache 命中、失敗與無目標三種 terminal 狀態。"""
    scanned_entries = getattr(scan_results, "entries", {}).get(jar_path)
    if scanned_entries:
        return _extract_scanned_entries(
            jar_path,
            output_root,
            jar_filename_base,
            scanned_entries,
            budget,
            get_file_hash_fn,
            getattr(scan_results, "asset_winners", None),
            nested_output_name,
        )
    if jar_path in getattr(scan_results, "failed_jars", set()):
        return {"status": "error", "extracted": 0, "skipped": 0}
    if jar_path not in scan_results:
        return {"status": "success", "extracted": 0, "skipped": 0}
    return None


def _load_legacy_jar_results(
    jar_path: str,
    target_regex: re.Pattern,
    budget: ZipReadBudget,
    scan_results,
) -> dict[str, str | None]:
    """載入舊式 direct-entry 結果，供沒有 detailed scan entry 的 caller 相容使用。"""
    jar_path_obj = Path(jar_path)
    if scan_results is not None and jar_path_obj in scan_results:
        return scan_results[jar_path_obj]

    jar_results: dict[str, str | None] = {}
    with zipfile.ZipFile(jar_path, "r") as zf:
        for name in zf.namelist():
            raise_if_cancelled()
            if not target_regex.search(name):
                continue
            try:
                jar_results[name] = read_limited(
                    zf, name, MAX_TEXT_BYTES, budget=budget
                ).decode("utf-8")
            except UnicodeDecodeError:
                jar_results[name] = None
            except ArchiveBudgetError:
                raise  # 整包累計超限：交給外層中止這個 JAR（不是單一檔案過大）
            except ZipSizeError as size_err:
                log_error("略過過大檔案 %s: %s", name, size_err)
    return jar_results


def extract_from_jar_impl(
    jar_path: str,
    output_root: str,
    target_regex: re.Pattern,
    get_file_hash_fn: Callable[[bytes], str] = get_file_hash,
    scan_results: dict[Path, dict[str, str | None]] | None = None,
    *,
    nested_output_name: str | None = None,
) -> dict[str, Any]:
    """從單一 JAR 檔案提取符合目標正規表達式的檔案。

    效能優化：scan_results 由 caller 預先掃描後傳入，避免重複掃描目錄。

    Args:
        jar_path: JAR 檔案路徑
        output_root: 輸出根目錄
        target_regex: 目標正規表達式
        get_file_hash_fn: 檔案 HASH 計算函式（預設 SHA-256）
        scan_results: 預先掃描的 JAR 結果（由 run_extraction_process_impl 一次性掃描後傳入）
    Returns:
        包含 extracted/skipped 的統計字典
    """
    extracted_count = 0
    skipped_count = 0
    jar_filename_base = _normalize_jar_base_name(jar_path)
    jar_path_obj = Path(jar_path)
    # 同一個 JAR 的「預掃描 + 提取」共用同一份累計預算（防止大量合法大小成員的 ZIP bomb）。
    # 正式流程由 scan_jars 建立並隨 scan_results（ScanResults）傳入；
    # 直接呼叫本函式（沒有預掃描結果）時，才建立自己的預算供本函式的掃描與提取共用。
    scan_budgets = getattr(scan_results, "budgets", None) or {}
    budget = scan_budgets.get(jar_path_obj)
    if budget is None:
        budget = ZipReadBudget(label=jar_path_obj.name)

    if not jar_path_obj.exists():
        log_error("JAR 檔案不存在: %s", jar_path)
        return {"status": "error", "extracted": 0, "skipped": 0}

    try:
        scan_results = _ensure_scan_results(
            scan_results, jar_path_obj, target_regex, budget
        )
        cached_result = _extract_cached_entries_or_status(
            jar_path_obj,
            output_root,
            jar_filename_base,
            budget,
            get_file_hash_fn,
            scan_results,
            nested_output_name,
        )
        if cached_result is not None:
            return cached_result

        jar_results = _load_legacy_jar_results(
            jar_path, target_regex, budget, scan_results
        )

        with zipfile.ZipFile(jar_path, "r") as zf:
            for member in zf.infolist():
                raise_if_cancelled()
                if member.is_dir():
                    continue
                normalized_path = member.filename.replace("\\", "/")
                if not target_regex.search(normalized_path):
                    continue

                # 防止路徑遍歷（zip-slip）：最終路徑必須留在 output_root 內
                try:
                    if normalized_path.startswith("assets/"):
                        final_output_path = safe_join(output_root, normalized_path)
                    else:
                        final_mod_folder = (
                            nested_output_name or f"{jar_filename_base}_extracted"
                        )
                        final_output_path = safe_join(
                            output_root, final_mod_folder, normalized_path
                        )
                except UnsafePathError:
                    log_error(
                        "拒絕提取不安全路徑: %s（來源 %s）", normalized_path, jar_path
                    )
                    skipped_count += 1
                    continue

                if (
                    normalized_path in jar_results
                    and jar_results[normalized_path] is not None
                ):
                    source_data = jar_results[normalized_path].encode("utf-8")
                else:
                    try:
                        source_data = read_limited(
                            zf, member, MAX_FILE_BYTES, budget=budget
                        )
                    except ArchiveBudgetError:
                        raise  # 整包累計超限：交給外層中止這個 JAR
                    except ZipSizeError as size_err:
                        log_error("略過過大檔案 %s: %s", normalized_path, size_err)
                        skipped_count += 1
                        continue

                source_hash = get_file_hash_fn(source_data)
                raise_if_cancelled()

                if os.path.exists(final_output_path):
                    with open(final_output_path, "rb") as existing_file:
                        existing_hash = get_file_hash_fn(existing_file.read())
                    if source_hash == existing_hash:
                        skipped_count += 1
                        continue

                os.makedirs(os.path.dirname(final_output_path), exist_ok=True)
                with open(final_output_path, "wb") as target:
                    target.write(source_data)
                extracted_count += 1

        return {
            "status": "success",
            "extracted": extracted_count,
            "skipped": skipped_count,
        }
    except ArchiveBudgetError as e:
        # 累計讀取超過安全上限：停止處理這個 JAR（已寫出的檔案保留），回報錯誤但不影響其他 JAR
        log_error(
            "[extract] 略過 %s 剩餘內容（累計讀取超過安全上限）: %s",
            jar_path_obj.name,
            e,
        )
        return {
            "status": "error",
            "extracted": extracted_count,
            "skipped": skipped_count,
        }
    except Exception as e:  # noqa: BLE001
        log_error("[extract] %s >>> EXCEPTION: %s", jar_path_obj.name, e)
        return {"status": "error", "extracted": 0, "skipped": 0}


def run_extraction_process_impl(
    mods_dir: str,
    output_dir: str,
    target_regex: re.Pattern,
    process_name: str,
    *,
    find_jar_files_fn: Callable[[str], list[str]],
    extract_from_jar_fn: Callable[..., dict[str, Any]],
    progress_start: float = 0.0,
    progress_end: float = 1.0,
) -> Generator[dict[str, Any], None, None]:
    """實作：對 mods 目錄下所有 JAR 執行批量提取流程。

    使用執行緒池並行處理，並在提取過程中回傳進度。

    Args:
        mods_dir: Mod 資料夾路徑。
        output_dir: 輸出根目錄。
        target_regex: 用以比對要提取之檔案路徑的正規表達式。
        process_name: 處理類型名稱（如 "Lang"、"Patchouli Book"），用於日誌。
        find_jar_files_fn: 用以掃描 JAR 檔案的函式（供測試替換用）。
        extract_from_jar_fn: 用以對單一 JAR 提取檔案的函式（供測試替換用）。

    Yields:
        進度字典，包含 progress（progress_start~progress_end）欄位。
    """
    from translation_tool.utils.jar_browser import scan_jars

    if (
        mods_dir
        and output_dir
        and Path(mods_dir).resolve() == Path(output_dir).resolve()
    ):
        raise ValueError(
            "source and output directories must differ to prevent overwriting input files; "
            "choose a separate extraction output folder."
        )

    os.makedirs(output_dir, exist_ok=True)
    jar_files = find_jar_files_fn(mods_dir)
    total_jars = len(jar_files)
    nested_output_names = build_collision_safe_output_names(jar_files, mods_dir)
    _reconcile_extracted_wrappers(
        output_dir,
        jar_files,
        nested_output_names,
    )

    if total_jars == 0:
        log.info("在 '%s' 中未找到任何 .jar 檔案。", mods_dir)
        yield {"progress": 1.0}
        return

    log.info("開始從 %s 個 .jar 檔案中提取 %s 檔案...", total_jars, process_name)
    scan_span = progress_end - progress_start
    scan_progress_end = progress_start + scan_span * 0.2
    extraction_progress_start = scan_progress_end
    extraction_span = progress_end - extraction_progress_start
    yield {
        "progress": progress_start,
        "current": 0,
        "total": total_jars,
        "log": "[掃描階段] 開始掃描 JAR 檔案...",
    }

    import threading

    scan_done = threading.Event()
    scan_error = [None]  # 利用 list 可變特性跨執行緒傳遞
    scan_cancelled = [False]
    scan_results_local = [{}]  # [0] = dict | None
    scan_progress = [0]

    def _scan_in_background():
        try:
            # 傳入與實際處理相同的清單（含子目錄 JAR），預掃描才不會與提取階段脫節
            scan_results_local[0] = scan_jars(
                jar_dir=Path(mods_dir),
                patterns=[target_regex.pattern],
                jar_files=jar_files,
                processed_callback=lambda processed, _total: scan_progress.__setitem__(
                    0, processed
                ),
            )
        except TaskCancelled:
            scan_cancelled[0] = True
        except Exception as e:  # noqa: BLE001
            scan_error[0] = e
        finally:
            scan_done.set()

    # run_in_context：背景預掃描寫出的後台記錄要帶著建立它的任務歸屬
    scan_thread = threading.Thread(
        target=run_in_context(_scan_in_background), name="scan-jars-bg", daemon=True
    )
    scan_thread.start()

    try:
        # 輪詢等待 scan 完成，每 0.5s 检查一次
        import time

        scan_start = time.time()
        last_yielded_at = 0.0
        while not scan_done.is_set():
            if is_cancelled():
                # 子執行緒有 bounded checkpoints；等它實際收斂後再讓 operation terminal。
                scan_done.wait(timeout=0.1)
                continue
            elapsed = time.time() - scan_start
            # 節流 yield：避免 100+ JAR × 30s 掃描產生 ~60 條重複訊息淹沒日誌
            if elapsed - last_yielded_at >= YIELD_INTERVAL:
                last_yielded_at = elapsed
                log.info(
                    "[scan_jars] background scanning... elapsed=%.1fs, jar_count=%s",
                    elapsed,
                    total_jars,
                )
                yield {
                    "progress": progress_start
                    + scan_span * 0.2 * scan_progress[0] / total_jars,
                    "current": scan_progress[0],
                    "total": total_jars,
                    "log": f"[掃描階段] 已掃描 {scan_progress[0]}/{total_jars} 個 JAR ({elapsed:.0f}s)...",
                }
            scan_done.wait(timeout=0.5)
        # 最後一次 yield 確保 UI 收到完成訊號
        elapsed = time.time() - scan_start
        if (
            not scan_error[0]
            and not scan_cancelled[0]
            and elapsed - last_yielded_at >= 0
        ):
            last_yielded_at = elapsed
            yield {
                "progress": scan_progress_end,
                "current": total_jars,
                "total": total_jars,
                "log": f"[掃描階段] 已掃描 {total_jars}/{total_jars} 個 JAR ({elapsed:.0f}s)...",
            }
    finally:
        # Generator.close() can occur while suspended at either progress yield.
        # Always join the scanner before allowing the owning operation to finish.
        scan_thread.join()
    elapsed_total = time.time() - scan_start

    if scan_cancelled[0] or is_cancelled():
        raise TaskCancelled()

    if scan_error[0]:
        log_error("[scan_jars] background scan failed: %s", scan_error[0])
        scan_progress_value = progress_start + scan_span * 0.2 * (
            scan_progress[0] / total_jars
        )
        yield {
            "progress": scan_progress_value,
            "current": scan_progress[0],
            "total": total_jars,
            "error": True,
            "log": f"[錯誤] 掃描失敗（已完成 {scan_progress[0]}/{total_jars}）：{scan_error[0]}",
        }
        return

    all_scan_results = scan_results_local[0]
    # 預掃描過的 JAR 數（每個被掃描的 JAR 都有預算）與「含可提取內容」的數量分開記錄，
    # 兩者都要能對上 total_jars（issue #111）。
    scanned_jars = len(getattr(all_scan_results, "budgets", None) or {}) or len(
        all_scan_results
    )
    log.info(
        "[scan_jars] 完成，共預掃描 %s / %s 個 JAR（其中 %s 個含可提取內容），耗時 %.1fs",
        scanned_jars,
        total_jars,
        len(all_scan_results),
        elapsed_total,
    )
    yield {
        "progress": extraction_progress_start,
        "current": 0,
        "total": len(all_scan_results),
        "log": f"[提取階段] 開始提取 ({len(all_scan_results)} 個含目標內容的 JAR)...",
    }

    eligible_jars = [jar for jar in jar_files if Path(jar) in all_scan_results]
    eligible_total = len(eligible_jars)
    processed_count = 0
    total_extracted = 0
    skipped_scan_jars = {
        path for path in getattr(all_scan_results, "skipped_jars", set())
    }
    total_skipped = len(skipped_scan_jars - set(eligible_jars))
    failed_jars: list[str] = [
        path.name for path in getattr(all_scan_results, "failed_jars", set())
    ]
    partial_scan_jars = sorted(
        getattr(all_scan_results, "partial_jars", set()), key=lambda path: path.name
    )
    for partial_jar in partial_scan_jars:
        skipped_nested = getattr(all_scan_results, "skipped_nested", {}).get(
            partial_jar, 0
        )
        truncated_nested = getattr(all_scan_results, "truncated_nested", {}).get(
            partial_jar, 0
        )
        yield {
            "progress": extraction_progress_start,
            "current": 0,
            "total": eligible_total,
            "log": (
                f"[WARNING] 部分掃描 {partial_jar.name}："
                f"略過 nested={skipped_nested}，深度截斷={truncated_nested}"
            ),
        }
    for failed_jar in failed_jars:
        yield {
            "progress": extraction_progress_start,
            "current": 0,
            "total": eligible_total,
            "log": f"[ERROR] 無法掃描 {failed_jar}（JAR 可能已損毀或超出安全讀取上限）",
        }
    config = load_config()
    max_workers = resolve_worker_count("jar_extract", config)
    log.info(
        "[workers] config=%r, actual=%s (cpu_count=%s; 設定無效或缺漏時用 CPU/2，上限 32)",
        configured_worker_value(config),
        max_workers,
        os.cpu_count() or 2,
    )

    import time as time_module

    _ex_start = time_module.time()

    submit_times = {}

    def submit_extraction(executor, jar_path):
        raise_if_cancelled()
        submit_time = time_module.time()
        nested_output_name = nested_output_names.get(Path(jar_path))
        if nested_output_name is None:
            future = executor.submit(
                extract_from_jar_fn,
                jar_path,
                output_dir,
                target_regex,
                all_scan_results,
            )
        else:
            future = executor.submit(
                extract_from_jar_fn,
                jar_path,
                output_dir,
                target_regex,
                all_scan_results,
                nested_output_name,
            )
        submit_times[future] = submit_time
        return future

    with (
        ContextThreadPoolExecutor(max_workers=max_workers) as executor,
        bounded_as_completed(
            executor,
            eligible_jars,
            submit_extraction,
            max_in_flight=max_workers * 2,
        ) as completed,
    ):
        for future, jar_path in completed:
            submit_time = submit_times.pop(future)
            _t_done = time_module.time()
            wall_time = _t_done - submit_time
            queue_time = submit_time - _ex_start
            processed_count += 1
            prog = extraction_progress_start + extraction_span * (
                processed_count / eligible_total if eligible_total else 1.0
            )
            try:
                result = future.result()
                jar_name = os.path.basename(jar_path)
                if result["status"] != "success":
                    # 損毀或無法讀取的 JAR 必須計入失敗，不能被當成成功
                    failed_jars.append(jar_name)
                    yield {
                        "progress": prog,
                        "current": processed_count,
                        "total": eligible_total,
                        "log": f"[ERROR] 無法提取 {jar_name}（JAR 可能已損毀或無法讀取）",
                    }
                    continue
                total_extracted += result["extracted"]
                total_skipped += result["skipped"]
                log.info(
                    "[%s/%s] %s queue=%.1fs wall=%.1fs",
                    processed_count,
                    eligible_total,
                    os.path.basename(jar_path),
                    queue_time,
                    wall_time,
                )
                yield {
                    "progress": prog,
                    "current": processed_count,
                    "total": eligible_total,
                    "log": f"[{processed_count}/{eligible_total}] {os.path.basename(jar_path)}",
                }
            except Exception as exc:  # noqa: BLE001
                failed_jars.append(os.path.basename(jar_path))
                # 第一行與下面 yield 給 UI 的文字相同，鏡像時後台不會重複記錄
                log_error(
                    "[ERROR] 提取 %s 時產生例外\n原因：%r (wall=%.1fs)",
                    os.path.basename(jar_path),
                    exc,
                    wall_time,
                )
                yield {
                    "progress": prog,
                    "current": processed_count,
                    "total": eligible_total,
                    "log": f"[ERROR] 提取 {os.path.basename(jar_path)} 時產生例外",
                }

    summary = (
        f"--- {process_name} 提取完成！ ---\n已檢查 {total_jars} 個 JAR，"
        f"其中 {processed_count}/{eligible_total} 個含可提取內容。\n"
        f"  - 新提取或更新的檔案: {total_extracted} 個\n"
        f"  - 因內容相同而跳過的檔案: {total_skipped} 個"
    )
    if failed_jars:
        summary += f"\n  - 無法提取的 JAR: {len(failed_jars)} 個（{', '.join(failed_jars[:10])}"
        summary += "…）" if len(failed_jars) > 10 else "）"
    if partial_scan_jars:
        summary += f"\n  - 部分掃描的 JAR: {len(partial_scan_jars)} 個"
    log.info(summary)
    final = {
        "progress": progress_end,
        "current": processed_count,
        "total": eligible_total,
        "log": summary,
        "stats": {
            "success": total_extracted,
            "warnings": total_skipped,
            "failures": len(failed_jars),
            "total_files": total_extracted,
            "scanned_jars": total_jars,
            "eligible_jars": eligible_total,
            "partial_jars": len(partial_scan_jars),
        },
    }
    yield final
