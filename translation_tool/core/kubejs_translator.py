"""translation_tool/core/kubejs_translator.py 模組。

用途：作為 KubeJS pipeline 的相容入口，保留 step orchestration 與對外 API。
維護注意：path / io / clean helpers 已拆到 kubejs_translator_* 子模組。
"""

from __future__ import annotations

import hashlib
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import orjson

from translation_tool.core.kubejs_translator_clean import (
    clean_kubejs_from_raw_impl,
    deep_merge_3way_flat_impl,
    is_filled_text_impl,
    prune_en_by_tw_flat_impl,
)
from translation_tool.core.kubejs_translator_io import (
    read_json_dict_orjson_impl,
    write_json_orjson_impl,
)
from translation_tool.core.kubejs_translator_paths import resolve_kubejs_root_impl
from translation_tool.core.kubejs_translator_state import (
    STATE_VERSION,
    commit_staging_run,
    copy_snapshot_tree,
    create_run_layout,
    fingerprint_source,
    load_current_manifest,
    new_run_id,
    output_lock,
    pipeline_state_root,
    run_root_for,
    source_identity,
    sync_managed_tree,
    write_current_manifest,
)
from translation_tool.utils.cancellation import is_cancelled, raise_if_cancelled
from translation_tool.utils.log_unit import (
    get_formatted_duration,
    log_debug,
    log_info,
    log_warning,
    progress,
)
from translation_tool.utils.text_processor import safe_convert_text


def _is_filled_text(v: Any) -> bool:
    """判斷傳入值是否為有實質內容的文字（排除空字串與 {xxx} 格式的語言參考）。

    Args:
        v: 任意型別的值。
    Returns:
        bool: 是有效文字內容則回傳 True，否則回傳 False。
    """
    return is_filled_text_impl(v)


def deep_merge_3way_flat(tw: dict, cn: dict, en: dict) -> dict:
    """對三個語系的扁平鍵值字典做三方合併，優先順序：zh_tw > zh_cn（轉繁）> en_us。

    Args:
        tw: 繁體中文鍵值對。
        cn: 簡體中文鍵值對（會自動轉為繁體）。
        en: 英文鍵值對（作為最後備選）。
    Returns:
        dict: 合併後的鍵值對字典。
    """
    return deep_merge_3way_flat_impl(tw, cn, en, safe_convert_text_fn=safe_convert_text)


def prune_en_by_tw_flat(en_map: dict, tw_available: dict) -> dict:
    """從英文鍵值地圖中移除已有中文（繁體）內容的項目，產生待翻譯清單。

    Args:
        en_map: 英文鍵值對。
        tw_available: 可用的繁體中文鍵值對。
    Returns:
        dict: 過濾後只剩下尚無中文翻譯的英文鍵值對。
    """
    return prune_en_by_tw_flat_impl(en_map, tw_available)


def _read_json_dict_orjson(path: Path) -> dict:
    """使用 orjson 讀取 JSON 檔案，自動處理 BOM 與結尾多餘逗號。

    Args:
        path: 要讀取的 JSON 檔案路徑。
    Returns:
        dict: 解析後的字典，解析失敗時回傳空字典。
    """
    return read_json_dict_orjson_impl(path)


def _write_json_orjson(path: Path, data: dict) -> None:
    """使用 orjson 將字典以格式化（縮排 2 層）寫入 JSON 檔案。

    Args:
        path: 目標檔案路徑，父目錄不存在時會自動建立。
        data: 要寫入的字典資料。
    """
    write_json_orjson_impl(path, data)


def clean_kubejs_from_raw(
    base_dir: str,
    *,
    output_dir: str | None = None,
    raw_dir: str | None = None,
    pending_root: str | None = None,
    final_root: str | None = None,
    previous_final_root: str | None = None,
    manual_final_root: str | None = None,
    previous_final_owned: dict[str, str] | None = None,
    current_source_provenance: dict[str, Any] | None = None,
    previous_source_provenance: dict[str, Any] | None = None,
) -> dict:
    """將 KubeJS 原始提取資料進行清理與三方合併，產出待翻譯與完成品目錄。

    Args:
        base_dir: Modpack 根目錄。
        output_dir: 輸出根目錄（預設為 base_dir/Output）。
        raw_dir: 原始 lang 檔案目錄（預設為 output_dir/kubejs/raw/kubejs）。
        pending_root: 待翻譯檔案輸出目錄（預設為 output_dir/kubejs/待翻譯/kubejs）。
        final_root: 完成品輸出目錄（預設為 output_dir/kubejs/完成/kubejs）。
    Returns:
        dict: 包含處理結果的摘要（群組數、寫入檔案數等）。
    """
    return clean_kubejs_from_raw_impl(
        base_dir,
        output_dir=output_dir,
        raw_dir=raw_dir,
        pending_root=pending_root,
        final_root=final_root,
        previous_final_root=previous_final_root,
        manual_final_root=manual_final_root,
        previous_final_owned=previous_final_owned,
        current_source_provenance=current_source_provenance,
        previous_source_provenance=previous_source_provenance,
        read_json_dict_fn=_read_json_dict_orjson,
        write_json_fn=_write_json_orjson,
        safe_convert_text_fn=safe_convert_text,
        log_debug_fn=log_debug,
        log_info_fn=log_info,
    )


def resolve_kubejs_root(input_dir: str, *, max_depth: int = 4) -> Path:
    """自動解析 KubeJS 根目錄，優先傳回包含 client_scripts 的候選目錄。

    Args:
        input_dir: 起始搜尋目錄（可為 modpack 根目錄或直接為 kubejs 目錄）。
        max_depth: 最大搜尋深度（預設 4）。
    Returns:
        Path: 偵測到的 KubeJS 目錄路徑；若找不到則回傳起始目錄本身。
    """
    return resolve_kubejs_root_impl(input_dir, max_depth=max_depth)


def step1_extract_and_clean(
    *,
    pack_or_kubejs_dir: str,
    raw_dir: str,
    pending_dir: str,
    final_dir: str,
    session=None,
    pre_extracted_summary: dict[str, Any] | None = None,
    previous_final_root: str | None = None,
    manual_final_root: str | None = None,
    previous_final_owned: dict[str, str] | None = None,
    previous_source_provenance: dict[str, Any] | None = None,
    progress_base: float = 0.0,
    progress_span: float = 0.33,
) -> dict[str, Any]:
    """KubeJS Pipeline 步驟一：從 KubeJS 目錄提取文字並執行清理與三方合併。

    Args:
        pack_or_kubejs_dir: Modpack 根目錄或直接為 kubejs 目錄的路徑。
        raw_dir: 原始提取文字的輸出目錄。
        pending_dir: 待翻譯檔案的輸出目錄。
        final_dir: 已翻譯完成品的輸出目錄。
        session: 進度 session（可為 None）。
        progress_base: 進度條起始值（預設 0.0）。
        progress_span: 進度條範圍（預設 0.33）。
    Returns:
        Dict[str, Any]: 包含 extract、clean、kubejs_dir、raw_dir、pending_dir、final_dir 的結果字典。
    """
    kubejs_dir_path = Path(resolve_kubejs_root(pack_or_kubejs_dir))
    log_info(f"\n🔎 [KubeJS] 確定 KubeJS 目錄為: {kubejs_dir_path}")

    log_info(f"📦 [KubeJS] 步驟 1-1：正在提取文字至 -> {raw_dir}")
    from translation_tool.plugins.kubejs.kubejs_tooltip_extract import (
        extract as kjs_extract,
    )

    extract_result = pre_extracted_summary or kjs_extract(
        source_dir=str(kubejs_dir_path),
        output_dir=str(Path(raw_dir).resolve()),
        session=session,
        progress_base=progress_base,
        progress_span=progress_span * 0.7,
    )
    if extract_result.get("errors_count", 0):
        raise RuntimeError(
            f"KubeJS 抽取有 {extract_result['errors_count']} 個檔案失敗；本輪快照未提交"
        )
    log_info(
        f"✅ [KubeJS] 提取完成: 檔案數={extract_result.get('extracted_files')} 總鍵值數={extract_result.get('extracted_keys_total')}"
    )

    log_info("🧹 [KubeJS] 步驟 1-2：執行清理並分類 (三方合併邏輯)")
    modpack_root = str(kubejs_dir_path.parent)

    clean_result = clean_kubejs_from_raw(
        base_dir=modpack_root,
        raw_dir=str(Path(raw_dir).resolve()),
        pending_root=str(Path(pending_dir).resolve()),
        final_root=str(Path(final_dir).resolve()),
        previous_final_root=previous_final_root,
        manual_final_root=manual_final_root,
        previous_final_owned=previous_final_owned,
        current_source_provenance=(pre_extracted_summary or {}).get(
            "source_provenance"
        ),
        previous_source_provenance=previous_source_provenance,
    )

    progress(session, min(progress_base + progress_span, 0.999))

    return {
        "extract": extract_result,
        "clean": clean_result,
        "kubejs_dir": str(kubejs_dir_path),
        "raw_dir": str(Path(raw_dir).resolve()),
        "pending_dir": str(Path(pending_dir).resolve()),
        "final_dir": str(Path(final_dir).resolve()),
    }


def step2_translate_lm(
    *,
    pending_dir: str,
    output_dir: str | None = None,
    translated_dir: str | None = None,
    session=None,
    progress_base: float = 0.33,
    progress_span: float = 0.33,
    dry_run: bool = False,
    write_new_cache: bool = True,
) -> dict[str, Any]:
    """KubeJS Pipeline 步驟二：呼叫 Gemini API 將待翻譯文字翻譯為繁體中文。

    Args:
        pending_dir: 待翻譯檔案目錄（即 step1 的 pending_dir）。
        output_dir: 翻譯結果輸出目錄（與 translated_dir 二選一）。
        translated_dir: 翻譯結果輸出目錄（與 output_dir 二選一）。
        session: 進度 session（可為 None）。
        progress_base: 進度條起始值（預設 0.33）。
        progress_span: 進度條範圍（預設 0.33）。
        dry_run: 是否為僅分析不回傳 API 的測試模式（預設 False）。
        write_new_cache: 是否寫入新的翻譯快取（預設 True）。
    Returns:
        Dict[str, Any]: 翻譯結果統計（檔案數、總 key 數、快取命中/未命中等）。
    """
    out_arg = output_dir or translated_dir
    if not out_arg:
        raise ValueError("step2_translate_lm: 必須提供 output_dir 或 translated_dir")

    log_info(
        f"🧠 [KubeJS] Step2 LM translate: {pending_dir} -> {out_arg} (dry_run={dry_run}, write_new_cache={write_new_cache})"
    )

    from translation_tool.plugins.kubejs.kubejs_tooltip_lmtranslator import (
        translate_kubejs_pending_to_zh_tw,
    )

    in_dir = str(Path(pending_dir).resolve())
    out_dir = str(Path(out_arg).resolve())

    class _ProgressProxy:
        def __init__(self, parent, base: float, span: float):
            self.parent = parent
            self.base = float(base)
            self.span = float(span)

        def set_progress(self, p: float):
            if not self.parent or not hasattr(self.parent, "set_progress"):
                return
            try:
                p = 0.0 if p is None else float(p)
                if p < 0:
                    p = 0.0
                elif p > 1:
                    p = 1.0
                self.parent.set_progress(self.base + p * self.span)
            except Exception:  # noqa: BLE001, S110 - UI 進度回報失敗不可中斷翻譯
                pass

        def set_status(self, msg: str):
            if self.parent and hasattr(self.parent, "set_status"):
                try:
                    self.parent.set_status(msg)
                except Exception:  # noqa: BLE001, S110 - UI 狀態回報失敗不可中斷翻譯
                    pass

    proxy = _ProgressProxy(session, progress_base, progress_span)

    result = translate_kubejs_pending_to_zh_tw(
        pending_dir=in_dir,
        output_dir=out_dir,
        session=proxy,
        dry_run=bool(dry_run),
        write_new_cache=bool(write_new_cache),
    )

    if session and hasattr(session, "set_progress"):
        try:
            session.set_progress(progress_base + progress_span)
        except Exception:  # noqa: BLE001, S110 - UI 進度回報失敗不可中斷翻譯
            pass

    return result


def step3_inject(
    *,
    pack_or_kubejs_dir: str,
    src_dir: str,
    final_dir: str,
    source_roots: dict[str, str] | None = None,
    source_order: list[str] | None = None,
    pending_root: str | None = None,
    session=None,
    progress_base: float = 0.66,
    progress_span: float = 0.33,
) -> dict[str, Any]:
    """KubeJS Pipeline 步驟三：將翻譯後的 JSON 文字注入回 KubeJS 目錄。

    Args:
        pack_or_kubejs_dir: Modpack 根目錄或 kubejs 目錄路徑。
        src_dir: 翻譯後的 JSON 檔案來源目錄。
        final_dir: 最終完成品輸出目錄。
        session: 進度 session（可為 None）。
        progress_base: 進度條起始值（預設 0.66）。
        progress_span: 進度條範圍（預設 0.33）。
    Returns:
        Dict[str, Any]: 注入結果摘要。
    """
    kubejs_dir = resolve_kubejs_root(pack_or_kubejs_dir)
    log_info(f"⚡[KubeJS] 步驟 3：開始注入翻譯 -> 目標目錄: {final_dir}")

    from translation_tool.plugins.kubejs.kubejs_tooltip_inject import (
        inject as kjs_inject,
    )

    return kjs_inject(
        str(kubejs_dir),
        str(Path(src_dir).resolve()),
        str(Path(final_dir).resolve()),
        session=session,
        progress_base=progress_base,
        progress_span=progress_span,
        source_roots=source_roots,
        source_order=source_order,
        pending_root=pending_root,
    )


def _aggregate_source_snapshots(
    run_root: Path, sources: list[dict[str, Any]]
) -> dict[str, Any]:
    """Merge lang files by exact file/key identity and isolate script origins."""
    raw_root = run_root / "raw" / "kubejs"
    raw_root.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    source_provenance: dict[str, dict[str, dict[str, dict[str, str]]]] = {}
    key_winners: dict[tuple[str, str], dict[str, Any]] = {}
    extracted_files = 0
    extracted_keys = 0

    for source in sorted(sources, key=lambda item: int(item["import_order"])):
        source_raw = run_root / Path(source["snapshot"])
        for path in sorted(source_raw.rglob("*.json")):
            relative = path.relative_to(source_raw)
            if "client_scripts" in {part.lower() for part in relative.parts}:
                relative = Path("_sources") / source["storage_id"] / relative
            relative_key = relative.as_posix()
            try:
                incoming = orjson.loads(path.read_bytes())
            except (OSError, orjson.JSONDecodeError) as exc:
                raise ValueError(f"無法讀取 KubeJS 抽取快照 {path}: {exc}") from exc
            if not isinstance(incoming, dict):
                raise TypeError(f"KubeJS 抽取快照不是 JSON object：{path}")
            extracted_files += 1
            extracted_keys += len(incoming)
            current = files.setdefault(relative_key, {})
            language = (
                path.stem.lower()
                if "lang" in {part.lower() for part in relative.parts}
                and path.stem.lower() in {"en_us", "zh_cn", "zh_tw"}
                else None
            )
            for key, value in incoming.items():
                if key not in current:
                    current[key] = value
                    key_winners[(relative_key, str(key))] = source
                    if language:
                        value_hash = hashlib.sha256(
                            orjson.dumps(value, option=orjson.OPT_SORT_KEYS)
                        ).hexdigest()
                        source_provenance.setdefault(relative_key, {}).setdefault(
                            str(key), {}
                        )[language] = {
                            "identity": str(source["identity"]),
                            "value_hash": value_hash,
                        }
                elif current[key] != value:
                    winner = key_winners.get((relative_key, str(key)), {})
                    conflicts.append(
                        {
                            "relative_path": relative_key,
                            "key": str(key),
                            "source_a": {
                                "identity": str(winner.get("identity", "unknown")),
                                "root": str(winner.get("root", "unknown")),
                                "value_hash": hashlib.sha256(
                                    orjson.dumps(
                                        current[key], option=orjson.OPT_SORT_KEYS
                                    )
                                ).hexdigest(),
                            },
                            "source_b": {
                                "identity": str(source.get("identity", "unknown")),
                                "root": str(source.get("root", "unknown")),
                                "value_hash": hashlib.sha256(
                                    orjson.dumps(value, option=orjson.OPT_SORT_KEYS)
                                ).hexdigest(),
                            },
                            "decision": "保留較早匯入來源的值",
                        }
                    )

    for relative, data in files.items():
        _write_json_orjson(raw_root / Path(relative), data)

    for conflict in conflicts:
        log_warning(
            "[KubeJS] 增量來源衝突，"
            f"{conflict['source_a']['root']} "
            f"(identity={conflict['source_a']['identity']}, "
            f"value_sha256={conflict['source_a']['value_hash']}) 與 "
            f"{conflict['source_b']['root']} "
            f"(identity={conflict['source_b']['identity']}, "
            f"value_sha256={conflict['source_b']['value_hash']}) "
            f"在 {conflict['relative_path']} 的 {conflict['key']} 不同；"
            f"決策：{conflict['decision']}"
        )
    if conflicts:
        log_warning(
            f"[KubeJS] 共發現 {len(conflicts)} 個來源衝突；已逐項列出來源與決策"
        )
    return {
        "output_dir": str(raw_root),
        "extracted_files": extracted_files,
        "extracted_keys_total": extracted_keys,
        "errors_count": 0,
        "source_conflicts": len(conflicts),
        "source_conflict_examples": [
            f"{conflict['relative_path']}:{conflict['key']}"
            for conflict in conflicts[:10]
        ],
        "source_conflict_details": conflicts,
        "source_provenance": source_provenance,
    }


def _collect_source_provenance(
    run_root: Path, sources: list[dict[str, Any]]
) -> dict[str, dict[str, dict[str, dict[str, str]]]]:
    """Rebuild the winning source identity and value hash for each lang key."""
    provenance: dict[str, dict[str, dict[str, dict[str, str]]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for source in sorted(sources, key=lambda item: int(item.get("import_order", 0))):
        source_raw = run_root / Path(source["snapshot"])
        for path in sorted(source_raw.rglob("*.json")):
            relative = path.relative_to(source_raw)
            if "lang" not in {part.lower() for part in relative.parts}:
                continue
            language = path.stem.lower()
            if language not in {"en_us", "zh_cn", "zh_tw"}:
                continue
            try:
                data = orjson.loads(path.read_bytes())
            except (OSError, orjson.JSONDecodeError) as exc:
                raise ValueError(f"無法讀取 KubeJS 語系快照 {path}: {exc}") from exc
            if not isinstance(data, dict):
                raise TypeError(f"KubeJS 語系快照不是 JSON object：{path}")
            relative_key = relative.as_posix()
            for key, value in data.items():
                identity = (relative_key, str(key), language)
                if identity in seen:
                    continue
                seen.add(identity)
                value_hash = hashlib.sha256(
                    orjson.dumps(value, option=orjson.OPT_SORT_KEYS)
                ).hexdigest()
                provenance.setdefault(relative_key, {}).setdefault(str(key), {})[
                    language
                ] = {
                    "identity": str(source["identity"]),
                    "value_hash": value_hash,
                }
    return provenance


def _sync_output_mirrors(
    output_root: Path,
    run_root: Path,
    manifest: dict[str, Any],
    categories: tuple[str, ...],
) -> list[dict[str, str]]:
    """Update familiar output folders only where the previous snapshot owns files."""
    layout = create_run_layout(run_root, manifest)
    targets = {
        "raw": (run_root / "raw" / "kubejs", output_root / "kubejs" / "raw" / "kubejs"),
        "pending": (
            run_root / "待翻譯" / "kubejs",
            output_root / "kubejs" / "待翻譯" / "kubejs",
        ),
        "translated": (
            layout["translated"],
            output_root / "kubejs" / "LM翻譯後" / "kubejs",
        ),
        "final": (
            layout["final"],
            output_root / "kubejs" / "完成" / "kubejs",
        ),
    }
    mirrors = manifest.setdefault("mirrors", {})
    conflicts: list[dict[str, str]] = []
    for category in categories:
        source_root, target_root = targets[category]
        old_owned = mirrors.get(category, {})
        if not isinstance(old_owned, dict):
            raise TypeError(f"KubeJS 狀態檔 {category} mirror ownership 格式無效")
        category_details: list[dict[str, str]] = []
        owned, _category_conflicts, removed = sync_managed_tree(
            source_root,
            target_root,
            old_owned,
            conflict_details=category_details,
        )
        mirrors[category] = owned
        conflicts.extend(
            {
                "category": category,
                "path": f"{category}/{detail['path']}",
                "conflict_type": detail["kind"],
                "preserved": "true",
                "mirror_path": str(target_root / detail["path"]),
                "snapshot_path": str(source_root / detail["path"]),
                "snapshot_exists": str((source_root / detail["path"]).exists()).lower(),
                "action": "比較公開檔與私有快照；確認後手動移走衝突檔或重新執行同步",
            }
            for detail in category_details
        )
        if removed:
            log_info(
                f"[KubeJS] 已清除 {category} 中 {removed} 個本流程管理且已過期的檔案"
            )
    for conflict in conflicts:
        log_warning(
            f"[KubeJS] 公開 {conflict['category']} 鏡像衝突"
            f"（{conflict['conflict_type']}）：{conflict['mirror_path']}；"
            f"已保留公開檔={conflict['preserved']}；"
            f"私有快照：{conflict['snapshot_path']} "
            f"（存在={conflict['snapshot_exists']}）；{conflict['action']}"
        )
    return conflicts


def _translation_output_gaps(pending_root: Path, translated_root: Path) -> list[str]:
    """Return pending keys missing from their expected Step 2 result files."""
    missing: list[str] = []
    for pending_path in sorted(pending_root.rglob("*.json")):
        try:
            pending_data = orjson.loads(pending_path.read_bytes())
        except (OSError, orjson.JSONDecodeError) as exc:
            raise ValueError(f"待翻譯 JSON 無法讀取：{pending_path}: {exc}") from exc
        if not isinstance(pending_data, dict):
            raise TypeError(f"待翻譯 JSON 不是 object：{pending_path}")
        relative = pending_path.relative_to(pending_root)
        if relative.parent.name.lower() == "lang" and relative.stem.lower() == "en_us":
            relative = relative.with_name("zh_tw.json")
        translated_path = translated_root / relative
        try:
            translated_data = orjson.loads(translated_path.read_bytes())
        except (OSError, orjson.JSONDecodeError):
            missing.append(f"{relative.as_posix()} (輸出檔不存在或無法讀取)")
            continue
        if not isinstance(translated_data, dict):
            missing.append(f"{relative.as_posix()} (輸出不是 JSON object)")
            continue
        absent = sorted(str(key) for key in pending_data if key not in translated_data)
        if absent:
            missing.append(
                f"{relative.as_posix()} (缺少 keys: {', '.join(absent[:10])})"
            )
    return missing


def _validate_final_snapshot(final_root: Path) -> None:
    """Reject an incomplete or unreadable injection candidate before commit."""
    if not final_root.is_dir():
        raise RuntimeError(f"KubeJS 完成品快照不存在：{final_root}")
    for path in sorted(final_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"KubeJS 完成品快照無法讀取：{path}: {exc}") from exc
        if path.suffix.lower() == ".json":
            try:
                value = orjson.loads(content)
            except orjson.JSONDecodeError as exc:
                raise RuntimeError(f"KubeJS 完成品 JSON 無效：{path}: {exc}") from exc
            if not isinstance(value, dict):
                raise RuntimeError(f"KubeJS 完成品 JSON 不是 object：{path}")


def run_kubejs_pipeline(
    *,
    input_dir: str,
    output_dir: str | None = None,
    session=None,
    dry_run: bool = False,
    step_extract: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    translator_fn: Callable[..., dict[str, Any]] | None = None,
    write_new_cache: bool = False,
    source_mode: str = "fresh",
) -> dict[str, Any]:
    """Run the KubeJS pipeline against an isolated, versioned source snapshot.

    ``fresh`` builds the snapshot only from the selected source. ``incremental``
    replaces that source's previous snapshot and retains the other imported
    sources. The familiar output folders are ownership-aware mirrors of the
    private snapshot; stale files are removed only when their recorded bytes
    are still present.
    """
    mode = str(source_mode).strip().lower()
    if mode not in {"fresh", "incremental"}:
        raise ValueError("source_mode 必須是 'fresh' 或 'incremental'")

    base = Path(input_dir).resolve()
    out_root = Path(output_dir).resolve() if output_dir else base / "Output"
    source_root = Path(resolve_kubejs_root(str(base))).resolve()
    state_root = pipeline_state_root(out_root)
    canonical_paths = {
        "raw": out_root / "kubejs" / "raw" / "kubejs",
        "pending": out_root / "kubejs" / "待翻譯" / "kubejs",
        "translated": out_root / "kubejs" / "LM翻譯後" / "kubejs",
        "final": out_root / "kubejs" / "完成" / "kubejs",
    }
    for path in canonical_paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    start_time = time.perf_counter()
    result: dict[str, Any] = {
        "source_mode": mode,
        "mirror_conflicts": [],
        "paths": {
            "input": str(base),
            "kubejs_source": str(source_root),
            **{key: str(path) for key, path in canonical_paths.items()},
        },
    }

    with output_lock(out_root):
        current = load_current_manifest(state_root)
        if step_extract:
            if not source_root.is_dir():
                raise FileNotFoundError(f"找不到 KubeJS 來源目錄：{source_root}")
            fingerprint, file_count, source_bytes = fingerprint_source(
                source_root, out_root
            )
            identity, storage_id = source_identity(source_root)
            prior_run = run_root_for(state_root, current) if current else None
            prior_sources = current.get("sources", []) if current else []
            prior_source_provenance = (
                _collect_source_provenance(prior_run, prior_sources)
                if prior_run is not None
                else {}
            )
            prior_mirrors = current.get("mirrors", {}) if current else {}
            if not isinstance(prior_mirrors, dict):
                raise ValueError("KubeJS 狀態檔 mirrors 格式無效")
            prior_final_owned = prior_mirrors.get("final", {})
            if not isinstance(prior_final_owned, dict):
                raise ValueError("KubeJS 狀態檔 final ownership 格式無效")
            prior_by_identity = {
                str(source.get("identity")): source
                for source in prior_sources
                if isinstance(source, dict)
            }
            prior_current = prior_by_identity.get(identity)

            run_id = new_run_id()
            staging_root = state_root / "staging" / run_id
            paths = create_run_layout(staging_root)
            stage_sources: list[dict[str, Any]] = []
            extraction_results: list[dict[str, Any]] = []
            try:
                if mode == "incremental" and prior_run is not None:
                    for source in sorted(
                        prior_sources, key=lambda item: int(item.get("import_order", 0))
                    ):
                        if source.get("identity") == identity:
                            continue
                        snapshot = str(source["snapshot"])
                        copy_snapshot_tree(
                            prior_run / Path(snapshot), staging_root / Path(snapshot)
                        )
                        stage_sources.append(dict(source))

                if mode == "incremental" and prior_current is not None:
                    import_order = int(prior_current.get("import_order", 0))
                else:
                    import_order = (
                        max(
                            (
                                int(source.get("import_order", 0))
                                for source in stage_sources
                            ),
                            default=-1,
                        )
                        + 1
                    )

                snapshot_relative = Path("sources") / storage_id / fingerprint / "raw"
                source_snapshot = staging_root / snapshot_relative
                can_reuse = (
                    mode == "incremental"
                    and prior_run is not None
                    and prior_current is not None
                    and prior_current.get("fingerprint") == fingerprint
                    and (prior_run / Path(prior_current["snapshot"])).is_dir()
                )
                if can_reuse:
                    copy_snapshot_tree(
                        prior_run / Path(prior_current["snapshot"]), source_snapshot
                    )
                else:
                    source_snapshot.parent.mkdir(parents=True, exist_ok=True)
                    from translation_tool.plugins.kubejs.kubejs_tooltip_extract import (
                        extract as kjs_extract,
                    )

                    extraction_result = kjs_extract(
                        source_dir=str(source_root),
                        output_dir=str(source_snapshot),
                        exclude_dir=str(out_root),
                        session=session,
                        progress_base=0.0,
                        progress_span=0.23,
                    )
                    extraction_results.append(extraction_result)
                    if extraction_result.get("errors_count", 0):
                        raise RuntimeError(
                            f"KubeJS 抽取有 {extraction_result['errors_count']} 個檔案失敗；本輪快照未提交"
                        )

                stage_sources.append(
                    {
                        "identity": identity,
                        "storage_id": storage_id,
                        "root": str(source_root),
                        "fingerprint": fingerprint,
                        "source_files": file_count,
                        "source_bytes": source_bytes,
                        "snapshot": snapshot_relative.as_posix(),
                        "import_order": import_order,
                    }
                )
                stage_sources.sort(key=lambda item: int(item["import_order"]))

                aggregate_summary = _aggregate_source_snapshots(
                    staging_root, stage_sources
                )
                previous_final = (
                    str(create_run_layout(prior_run, current)["final"])
                    if mode == "incremental" and prior_run is not None
                    else None
                )
                result["step1"] = step1_extract_and_clean(
                    pack_or_kubejs_dir=str(source_root),
                    raw_dir=str(paths["raw"]),
                    pending_dir=str(paths["pending"]),
                    final_dir=str(paths["final"]),
                    session=session,
                    pre_extracted_summary=aggregate_summary,
                    previous_final_root=previous_final,
                    manual_final_root=str(canonical_paths["final"]),
                    previous_final_owned=prior_final_owned,
                    previous_source_provenance=prior_source_provenance,
                    progress_base=0.23,
                    progress_span=0.10,
                )
                aggregate_summary.pop("source_provenance", None)
                result["step1"]["source_mode"] = mode
                result["step1"]["sources"] = [
                    {
                        "root": source["root"],
                        "source_files": source["source_files"],
                        "source_bytes": source["source_bytes"],
                        "import_order": source["import_order"],
                    }
                    for source in stage_sources
                ]
                result["step1"]["extractions"] = extraction_results
                result["step1"]["source_conflict_details"] = aggregate_summary.get(
                    "source_conflict_details", []
                )

                # Commit the complete Step 1 snapshot before updating public mirrors.
                runs_root = state_root / "runs"
                runs_root.mkdir(parents=True, exist_ok=True)
                committed_root = runs_root / run_id
                used_copy_fallback = commit_staging_run(staging_root, committed_root)
                if used_copy_fallback:
                    log_warning(
                        "[KubeJS] Windows 暫時封鎖快照目錄重新命名；已安全複製完成快照"
                    )
                current = {
                    "version": STATE_VERSION,
                    "run_id": run_id,
                    "stage": "cleaned",
                    "source_mode": mode,
                    "sources": stage_sources,
                    "mirrors": dict(current.get("mirrors", {})) if current else {},
                    "updated_at": time.time(),
                }
                write_current_manifest(state_root, current)
                result["mirror_conflicts"].extend(
                    _sync_output_mirrors(
                        out_root,
                        committed_root,
                        current,
                        ("raw", "pending", "translated", "final"),
                    )
                )
                write_current_manifest(state_root, current)
                paths = create_run_layout(committed_root, current)
                result["paths"]["snapshot"] = str(committed_root)
            except BaseException:
                if staging_root.exists():
                    shutil.rmtree(staging_root, ignore_errors=True)
                raise
        else:
            if current is None:
                raise ValueError("目前沒有 KubeJS 快照；請先執行 Step 1 抽取")
            committed_root = run_root_for(state_root, current)
            paths = create_run_layout(committed_root, current)
            stage_sources = list(current.get("sources", []))
            result["step1"] = {"skipped": True, "reason": "step_extract disabled"}
            result["paths"]["snapshot"] = str(committed_root)

        if current is None:
            raise RuntimeError("KubeJS 快照未建立")

        def count_json_keys(root: Path) -> int:
            count = 0
            for path in root.rglob("*.json"):
                try:
                    value = orjson.loads(path.read_bytes())
                except (OSError, orjson.JSONDecodeError) as exc:
                    log_warning(f"[KubeJS] 統計時略過無法讀取的 JSON {path}: {exc!r}")
                    continue
                if isinstance(value, dict):
                    count += len(value)
            return count

        pending_count = count_json_keys(paths["pending"])
        result["pending_keys"] = pending_count
        log_info(f"🧾 [KubeJS] 本次快照共有 {pending_count} 個待翻譯項目")

        translated_json = sorted(paths["translated"].rglob("*.json"))
        translation_ran = False
        if pending_count == 0:
            result["step2"] = {"skipped": True, "reason": "pending keys = 0"}
            progress(session, 0.66)
        elif not step_translate:
            result["step2"] = {"skipped": True, "reason": "disabled"}
            progress(session, 0.66)
        else:
            raise_if_cancelled()
            if translator_fn is None:
                translator_fn = step2_translate_lm
            translated_stage_container = (
                committed_root / f".translated-stage-{new_run_id()}"
            )
            translated_stage = translated_stage_container / "kubejs"
            translated_stage.mkdir(parents=True, exist_ok=True)
            try:
                result["step2"] = translator_fn(
                    pending_dir=str(paths["pending"]),
                    output_dir=str(translated_stage),
                    session=session,
                    progress_base=0.33,
                    progress_span=0.33,
                    dry_run=bool(dry_run),
                    write_new_cache=bool(write_new_cache and not dry_run),
                )
                if not isinstance(result["step2"], dict):
                    result["step2"] = {"result": result["step2"]}
                translation_ran = not dry_run and not result["step2"].get("skipped")
                if is_cancelled():
                    translation_ran = False
                    result["step2"].update({"skipped": True, "reason": "cancelled"})
                if translation_ran:
                    gaps = _translation_output_gaps(paths["pending"], translated_stage)
                    if gaps:
                        result["step2"]["incomplete_outputs"] = gaps
                        raise RuntimeError(
                            "KubeJS Step 2 輸出不完整，未提交翻譯快照："
                            + "; ".join(gaps[:10])
                        )
                    translated_relative = (
                        Path("translated-versions") / new_run_id() / "kubejs"
                    )
                    translated_committed = committed_root / translated_relative
                    used_copy_fallback = commit_staging_run(
                        translated_stage, translated_committed
                    )
                    if used_copy_fallback:
                        log_warning(
                            "[KubeJS] Windows 暫時封鎖翻譯快照重新命名；已安全複製完成快照"
                        )
                    current["translated_snapshot"] = translated_relative.as_posix()
                    current["stage"] = "translated"
                    current["updated_at"] = time.time()
                    paths = create_run_layout(committed_root, current)
                    translated_json = sorted(paths["translated"].rglob("*.json"))
                    write_current_manifest(state_root, current)
                    result["mirror_conflicts"].extend(
                        _sync_output_mirrors(
                            out_root, committed_root, current, ("translated",)
                        )
                    )
                    write_current_manifest(state_root, current)
            except BaseException:
                if translated_stage_container.exists():
                    shutil.rmtree(translated_stage_container, ignore_errors=True)
                raise
            finally:
                if translated_stage_container.exists():
                    shutil.rmtree(translated_stage_container, ignore_errors=True)
            progress(session, 0.66)

        if not step_translate and pending_count > 0:
            log_info("⏭️ [KubeJS] Step2 已關閉；不使用舊翻譯結果")

        if not step_inject:
            result["step3"] = {"skipped": True, "reason": "disabled"}
        elif dry_run:
            result["step3"] = {"skipped": True, "reason": "dry_run"}
        elif not translation_ran:
            reason = "cancelled" if is_cancelled() else "no current translation output"
            result["step3"] = {"skipped": True, "reason": reason}
        elif not translated_json:
            result["step3"] = {"skipped": True, "reason": "translation output is empty"}
        else:
            injected_stage_container = committed_root / f".final-inject-{new_run_id()}"
            injected_stage = injected_stage_container / "kubejs"
            source_roots = {
                str(source["storage_id"]): str(source["root"])
                for source in stage_sources
            }
            source_order = [
                str(source["storage_id"])
                for source in sorted(
                    stage_sources, key=lambda item: int(item["import_order"])
                )
            ]
            try:
                copy_snapshot_tree(paths["final"], injected_stage)
                result["step3"] = step3_inject(
                    pack_or_kubejs_dir=str(source_root),
                    src_dir=str(paths["translated"]),
                    final_dir=str(injected_stage),
                    source_roots=source_roots,
                    source_order=source_order,
                    pending_root=str(paths["pending"]),
                    session=session,
                    progress_base=0.66,
                    progress_span=0.33,
                )
                if is_cancelled():
                    result["step3"] = {"skipped": True, "reason": "cancelled"}
                else:
                    _validate_final_snapshot(injected_stage)
                    final_relative = Path("final-versions") / new_run_id() / "kubejs"
                    final_committed = committed_root / final_relative
                    used_copy_fallback = commit_staging_run(
                        injected_stage, final_committed
                    )
                    if used_copy_fallback:
                        log_warning(
                            "[KubeJS] Windows 暫時封鎖完成品快照重新命名；已安全複製完成快照"
                        )
                    current["final_snapshot"] = final_relative.as_posix()
                    current["stage"] = "injected"
                    current["updated_at"] = time.time()
                    paths = create_run_layout(committed_root, current)
                    write_current_manifest(state_root, current)
                    result["mirror_conflicts"].extend(
                        _sync_output_mirrors(
                            out_root, committed_root, current, ("final",)
                        )
                    )
                    write_current_manifest(state_root, current)
            except BaseException:
                if injected_stage_container.exists():
                    shutil.rmtree(injected_stage_container, ignore_errors=True)
                raise
            finally:
                if injected_stage_container.exists():
                    shutil.rmtree(injected_stage_container, ignore_errors=True)

        result["mirror_conflict_count"] = len(result["mirror_conflicts"])
        result["output_sync_status"] = (
            "conflicts" if result["mirror_conflicts"] else "synced"
        )
        if result["mirror_conflicts"]:
            log_warning(
                f"[KubeJS] 最新私有快照已完成；公開輸出有 {len(result['mirror_conflicts'])} 個鏡像衝突。"
                "請依上方路徑比較公開檔與快照，再決定是否移走衝突檔後重新執行。"
            )

    result["duration"] = get_formatted_duration(start_time)
    if is_cancelled():
        log_warning(f"⏹ [KubeJS] 已取消（目前快照保留） {result['duration']}")
    else:
        log_info(f"🎉 [KubeJS] 任務完成！ {result['duration']}")
    progress(session, 0.999)
    return result


__all__ = [
    "_is_filled_text",
    "_read_json_dict_orjson",
    "_write_json_orjson",
    "clean_kubejs_from_raw",
    "deep_merge_3way_flat",
    "prune_en_by_tw_flat",
    "resolve_kubejs_root",
    "run_kubejs_pipeline",
    "step1_extract_and_clean",
    "step2_translate_lm",
    "step3_inject",
]
