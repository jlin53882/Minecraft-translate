"""app/icon_index.py

用途：預建立 icon 索引（PR60 Phase 2）。
一次性建立 modid:key → (jar_path, png_path) 的 JSON 索引，
爾後每次開啟直接讀取 JSON，完全不做 JAR I/O，瞬間完成。
"""

from __future__ import annotations

import hashlib
import json
import uuid
import zipfile
from collections.abc import Iterator, Mapping
from pathlib import Path

from app.icon_runtime import get_runtime_asset_paths
from translation_tool.utils.app_paths import get_data_root
from translation_tool.utils.bounded_executor import bounded_as_completed
from translation_tool.utils.cancellation import raise_if_cancelled
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_info, log_warning
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor
from translation_tool.utils.worker_count import resolve_worker_count
from translation_tool.utils.zip_safety import (
    ArchiveBudgetError,
    ZipReadBudget,
    read_limited,
)

# ==================================================
# 核心資料結構
# ==================================================


class IconIndex(dict[str, str]):
    """Icon URI 索引，並保留每個來源 JAR 的獨立 mapping。"""

    def __init__(
        self,
        values: Mapping[str, str] | None = None,
        *,
        by_jar: Mapping[str, Mapping[str, str]] | None = None,
        complete_by_jar: Mapping[str, bool] | None = None,
    ) -> None:
        super().__init__(values or {})
        self.by_jar = (
            {jar_name: dict(jar_index) for jar_name, jar_index in by_jar.items()}
            if by_jar is not None
            else None
        )
        self.complete_by_jar = (
            {
                jar_name: bool(is_complete)
                for jar_name, is_complete in complete_by_jar.items()
            }
            if complete_by_jar is not None
            else ({jar_name: True for jar_name in self.by_jar} if self.by_jar else None)
        )

    def for_jar(self, jar_name: str) -> Mapping[str, str]:
        """Return the index belonging to one source JAR."""
        if self.by_jar is None:
            return {}
        return self.by_jar.get(jar_name, {})


def _jar_manifest(mods_dir: Path) -> list[dict[str, int | str]]:
    """Return a fast, deterministic signature for each JAR in a modpack."""
    manifest = []
    for jar_path in sorted(
        mods_dir.glob("*.jar"), key=lambda path: path.name.casefold()
    ):
        stat = jar_path.stat()
        manifest.append(
            {
                "name": jar_path.name,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    for asset_path in get_runtime_asset_paths(mods_dir):
        stat = asset_path.stat()
        manifest.append(
            {
                "name": str(asset_path.resolve()),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return manifest


def _compute_modpack_hash(
    mods_dir: Path, manifest: list[dict[str, int | str]] | None = None
) -> str:
    """Hash the modpack path and JAR metadata, not just the JAR filenames."""
    identity = {
        "modpack": str(mods_dir.resolve()),
        "jars": _jar_manifest(mods_dir) if manifest is None else manifest,
    }
    key = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _index_path_for_manifest(
    mods_dir: Path, manifest: list[dict[str, int | str]]
) -> Path:
    cache_dir = get_data_root() / ".icon_cache" / "icon_index"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{_compute_modpack_hash(mods_dir, manifest)}.json"


def get_index_path(mods_dir: Path) -> Path:
    """取得該 modpack 的索引檔路徑。"""
    return _index_path_for_manifest(mods_dir, _jar_manifest(mods_dir))


# ==================================================
# JAR → icon 解析（給 worker thread 用）
# ==================================================


def _strip_json_extras(text: str) -> str:
    """移除 JSON 字串以外的 ``//``、``/* */`` 註解與結尾逗號（遊戲的 GSON 讀取器容許這些）。"""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return _strip_json_trailing_commas("".join(out))


def _strip_json_trailing_commas(text: str) -> str:
    """移除字串以外、緊接 JSON 容器結尾的逗號。"""
    result: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            result.append(ch)
            if ch == "\\" and i + 1 < n:
                result.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            result.append(ch)
        elif ch == ",":
            next_index = i + 1
            while next_index < n and text[next_index].isspace():
                next_index += 1
            if next_index < n and text[next_index] in "}]":
                i += 1
                continue
            result.append(ch)
        else:
            result.append(ch)
        i += 1
    return "".join(result)


def loads_lang_json(content: str):
    """解析 en_us.json：容許原始控制字元、註解與結尾逗號，行為與遊戲讀取器一致。"""
    try:
        return json.loads(content, strict=False)
    except json.JSONDecodeError:
        return json.loads(_strip_json_extras(content), strict=False)


def _iter_entries_from_lang_files(
    zf: zipfile.ZipFile, budget: ZipReadBudget | None = None
) -> Iterator[tuple[str, str, str]]:
    """列舉每個 assets namespace 的英文 lang entries（modid, key, value）。"""
    members_by_modid: dict[str, list[str]] = {}
    for name in zf.namelist():
        raise_if_cancelled()
        parts = name.split("/")
        if (
            len(parts) != 4
            or parts[0] != "assets"
            or parts[2] != "lang"
            or parts[3] not in {"en_us.json", "en_us.lang"}
        ):
            continue
        members_by_modid.setdefault(parts[1], []).append(name)

    for modid in sorted(members_by_modid):
        raise_if_cancelled()
        # Prefer the production JSON contract; .lang is only a legacy fallback.
        candidates = sorted(
            members_by_modid[modid],
            key=lambda name: (not name.endswith(".json"), name),
        )
        for name in candidates:
            try:
                content = read_limited(zf, name, budget=budget).decode(
                    "utf-8-sig", errors="ignore"
                )
            except ArchiveBudgetError as exc:
                if budget is not None:
                    raise
                log_warning(f"[IconIndex] 略過無法讀取的 lang 檔 {name}: {exc!r}")
                continue
            except Exception as exc:  # noqa: BLE001 - 單一 lang 檔讀不出來就略過
                log_warning(f"[IconIndex] 略過無法讀取的 lang 檔 {name}: {exc!r}")
                continue

            if name.endswith(".json"):
                try:
                    entries = loads_lang_json(content)
                except json.JSONDecodeError as exc:
                    log_warning(
                        f"[IconIndex] 略過格式錯誤的 JSON lang 檔 {name}: {exc!r}"
                    )
                    continue
                if not isinstance(entries, dict):
                    log_warning(f"[IconIndex] 略過非 object 的 JSON lang 檔 {name}")
                    continue
                entries_to_yield = entries.items()
            else:
                parsed_entries = []
                for line in content.splitlines():
                    raise_if_cancelled()
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    idx = line.index("=")
                    parsed_entries.append((line[:idx].strip(), line[idx + 1 :].strip()))
                entries_to_yield = parsed_entries

            for key, value in entries_to_yield:
                if (
                    isinstance(key, str)
                    and isinstance(value, str)
                    and "." in key
                    and value
                ):
                    yield modid, key, value
            break  # 每個 namespace 只取第一個有效的英文 lang 檔


class JarIconIndex(dict[str, str]):
    """A per-JAR icon mapping plus whether the complete JAR was parsed."""

    def __init__(
        self, values: Mapping[str, str] | None = None, *, complete: bool = True
    ) -> None:
        super().__init__(values or {})
        self.complete = complete


def _process_single_jar(jar_path: Path, asset_catalog) -> JarIconIndex:
    """Worker: 處理單一 JAR，建立該 JAR 所有 entry 的 icon 索引。

    回傳：{key: icon_uri} — 該 JAR 內所有有 icon 的 key mapping
    """
    results: dict[str, str] = {}
    complete = True
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            raise_if_cancelled()
            budget = ZipReadBudget.for_icon_scan(jar_path.name)
            from app.icon_resolve import CONTENT_ICON_PREFIXES, hit_to_uri
            from app.views.icon_preview.icon_cache import _resolve_icon_from_catalog

            for modid, key, _value in _iter_entries_from_lang_files(zf, budget=budget):
                raise_if_cancelled()
                # 只處理有意義的 content key
                if key.split(".", 1)[0] not in CONTENT_ICON_PREFIXES:
                    continue

                result = _resolve_icon_from_catalog(
                    jar_path,
                    modid,
                    key,
                    asset_catalog,
                    current_zf=zf,
                    budget=budget,
                )
                if result:
                    results[key] = hit_to_uri(result)
    except ArchiveBudgetError:
        # 累計讀取超過安全上限（budget 已記錄警告）：保留已建立的部分索引，不中止整個索引建置
        complete = False
        log_warning(f"[IconIndex] {jar_path.name} 累計讀取超限，僅保留已解析的部分索引")
    except Exception as exc:  # noqa: BLE001 - 單一 JAR 索引失敗不中止整體，但要留下堆疊
        complete = False
        log_warning(
            f"[IconIndex] {jar_path.name} 索引建立失敗，僅保留已解析的部分索引：{exc!r}",
            exc_info=True,
        )
    return JarIconIndex(results, complete=complete)


# ==================================================
# 公開 API
# ==================================================


def build_icon_index(mods_dir: Path, progress_cb=None) -> IconIndex:
    """使用 ThreadPoolExecutor 建立 icon 索引（Phase 2 主體）。

    流程：
        1. 列舉所有 JAR 及每個 JAR 的 modid
        2. 8 threads 平行處理每個 JAR
        3. 合併所有結果為單一 JSON 索引
        4. 回傳索引；要寫入磁碟請使用 build_and_save_icon_index()

    回傳：{key: icon_uri} 完整索引
    """

    # Namespace 從 assets/<modid>/lang/en_us.* 取得，與即時掃描契約一致。
    jars = sorted(mods_dir.glob("*.jar"))
    total = len(jars)
    log_info(f"[IconIndex] 開始建立索引：{total} 個 JAR，使用 8 threads")

    index: dict[str, str] = {}
    index_by_jar: dict[str, dict[str, str]] = {}
    complete_by_jar: dict[str, bool] = {}
    owners: dict[str, str] = {}
    done = 0

    from app.views.icon_preview.icon_cache import ModpackAssetCatalog

    asset_catalog = ModpackAssetCatalog.from_mods_directory(mods_dir)

    max_workers = resolve_worker_count("icon_index", load_config())

    def submit_jar(executor, jar_path: Path):
        return executor.submit(_process_single_jar, jar_path, asset_catalog)

    try:
        with (
            ContextThreadPoolExecutor(max_workers=max_workers) as executor,
            bounded_as_completed(
                executor,
                jars,
                submit_jar,
                max_in_flight=max_workers * 2,
            ) as completed,
        ):
            for future, jar in completed:
                raise_if_cancelled()
                done += 1
                try:
                    jar_results = future.result()
                    index_by_jar[jar.name] = dict(jar_results)
                    complete_by_jar[jar.name] = bool(
                        getattr(jar_results, "complete", True)
                    )
                    jar_order = jar.name.casefold()
                    for key, uri in jar_results.items():
                        raise_if_cancelled()
                        # 同一 key 出現在多個 JAR：以 JAR 名稱排序最前者為準，
                        # 結果不再取決於執行緒完成順序。
                        if key not in index or jar_order < owners[key]:
                            index[key] = uri
                            owners[key] = jar_order
                    if progress_cb:
                        progress_cb(done, total)
                except Exception as ex:  # noqa: BLE001
                    log_warning(f"[IconIndex] JAR 處理失敗 {jar.name}: {ex!r}")
                if done % 50 == 0 or done == total:
                    log_info(
                        f"[IconIndex] 進度：{done}/{total} JARs，已建立 {len(index)} 個 icon 索引"
                    )
    finally:
        # The executor context joins running workers before shared ZIP handles close.
        asset_catalog.close()

    log_info(f"[IconIndex] 索引建立完成：{len(index)} 個 icon 進入索引")
    return IconIndex(
        index,
        by_jar=index_by_jar,
        complete_by_jar=complete_by_jar,
    )


def build_and_save_icon_index(mods_dir: Path, progress_cb=None) -> dict[str, str]:
    """Build and persist an index only if the source JAR set stayed unchanged."""
    manifest = _jar_manifest(mods_dir)
    index = build_icon_index(mods_dir, progress_cb=progress_cb)
    raise_if_cancelled()
    if _jar_manifest(mods_dir) != manifest:
        raise RuntimeError(
            "Mod JARs changed while the icon index was being built; retry."
        )
    _save_icon_index_for_manifest(mods_dir, index, manifest)
    return index


def save_icon_index(mods_dir: Path, index: Mapping[str, str]) -> Path:
    """將 icon 索引寫入磁碟（JSON 格式）。"""
    return _save_icon_index_for_manifest(mods_dir, index, _jar_manifest(mods_dir))


def _save_icon_index_for_manifest(
    mods_dir: Path,
    index: Mapping[str, str],
    manifest: list[dict[str, int | str]],
) -> Path:
    source_aware_index = index if isinstance(index, IconIndex) else IconIndex(index)
    version = (
        7
        if source_aware_index.complete_by_jar is not None
        else 6
        if source_aware_index.by_jar is not None
        else 5
    )
    idx_path = _index_path_for_manifest(mods_dir, manifest)
    data = {
        "version": version,
        "modpack": str(mods_dir.resolve()),
        "jars": manifest,
        "count": len(source_aware_index),
        "index": dict(source_aware_index),
    }
    if source_aware_index.by_jar is not None:
        if version == 7:
            data["index_by_jar"] = {
                jar_name: {
                    "complete": source_aware_index.complete_by_jar.get(jar_name, False),
                    "index": jar_index,
                }
                for jar_name, jar_index in source_aware_index.by_jar.items()
            }
        else:
            data["index_by_jar"] = source_aware_index.by_jar
    temp_path = idx_path.with_name(f"{idx_path.stem}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        temp_path.replace(idx_path)
    finally:
        temp_path.unlink(missing_ok=True)
    log_info(f"[IconIndex] 索引已儲存：{idx_path}")
    return idx_path


def _is_string_mapping(value) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(icon_uri, str)
        for key, icon_uri in value.items()
    )


def _load_v6_index(
    raw_index: dict, data: dict, expected_jars: set[str]
) -> IconIndex | None:
    """Validate the legacy per-JAR shape without trusting it for fast scans."""
    raw_by_jar = data["index_by_jar"]
    if not isinstance(raw_by_jar, dict) or set(raw_by_jar) != expected_jars:
        return None
    if not all(
        isinstance(jar_name, str) and _is_string_mapping(jar_index)
        for jar_name, jar_index in raw_by_jar.items()
    ):
        return None
    # Version 6 has no completeness bit, so it cannot suppress a direct scan.
    return IconIndex(raw_index)


def _load_v7_index(
    raw_index: dict, data: dict, expected_jars: set[str]
) -> IconIndex | None:
    """Load source-aware mappings only when every JAR was fully parsed."""
    raw_by_jar = data["index_by_jar"]
    if not isinstance(raw_by_jar, dict) or set(raw_by_jar) != expected_jars:
        return None
    complete_by_jar: dict[str, bool] = {}
    by_jar: dict[str, dict[str, str]] = {}
    for jar_name, jar_payload in raw_by_jar.items():
        if not isinstance(jar_name, str) or not isinstance(jar_payload, dict):
            return None
        is_complete = jar_payload.get("complete")
        jar_index = jar_payload.get("index")
        if not isinstance(is_complete, bool) or not _is_string_mapping(jar_index):
            return None
        complete_by_jar[jar_name] = is_complete
        by_jar[jar_name] = jar_index
    if not all(complete_by_jar.values()):
        log_warning("[IconIndex] 預建索引含未完整解析的 JAR，改為逐 JAR 解析")
        return None
    return IconIndex(
        raw_index,
        by_jar=by_jar,
        complete_by_jar=complete_by_jar,
    )


def load_icon_index(mods_dir: Path) -> IconIndex | None:
    """快速載入已建立的 icon 索引。找不到或格式不符回 None。"""
    manifest = _jar_manifest(mods_dir)
    idx_path = _index_path_for_manifest(mods_dir, manifest)
    if not idx_path.exists():
        return None
    try:
        data = json.loads(idx_path.read_text(encoding="utf-8"))
        version = data.get("version")
        if version not in {5, 6, 7} or data.get("jars") != manifest:
            return None
        if data.get("modpack") != str(mods_dir.resolve()):
            # modpack 路徑改了
            return None
        raw_index = data["index"]
        if not isinstance(raw_index, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in raw_index.items()
        ):
            return None
        if version == 5:
            loaded = IconIndex(raw_index)
        elif version == 6:
            loaded = _load_v6_index(
                raw_index,
                data,
                {path.name for path in mods_dir.glob("*.jar")},
            )
        else:
            loaded = _load_v7_index(
                raw_index,
                data,
                {path.name for path in mods_dir.glob("*.jar")},
            )
        if loaded is None:
            return None
        log_info(
            f"[IconIndex] 已載入索引：{data.get('count')} 個 icon（來源：{idx_path.name}）"
        )
        return loaded
    except Exception as ex:  # noqa: BLE001
        log_warning(f"[IconIndex] 索引載入失敗：{ex!r}")
        return None
