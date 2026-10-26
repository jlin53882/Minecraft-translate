"""圖示預覽頁的圖示提取與快取輔助函式（由 icon_preview_view.py 拆出，#114）。

含 JAR 內 model JSON 解析、圖示提取與批次處理、model index／L2 entries 快取與進度輔助。
測試要 monkeypatch 這些函式時，請 patch 本模組（呼叫者都在這裡查名稱）。
"""

import shutil
import unicodedata
import zipfile
from pathlib import Path

from app.icon_catalog import ModpackAssetCatalog
from app.icon_resolve import CONTENT_ICON_PREFIXES, IconHit, hit_to_uri, resolve_icon
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
)

# 只有這些內容 key 需要進行 model icon lookup。
# 不在清單裡的 key（如 _comment、advancements.*、recipe_type、jei.* 等）不該有 icon。
_CONTENT_ICON_PREFIXES = CONTENT_ICON_PREFIXES


def _key_needs_icon(key: str) -> bool:
    """判斷 key 是否為需要 icon 的遊戲內容。

    只有 item/block/entity 等前綴才需要 icon。
    metadata key（如 _comment、advancements.*、jei.*）完全不該有 icon。
    """
    if "." not in key:
        return False  # 完全沒有 namespace，不可能是遊戲內容
    prefix = key.split(".")[0]
    return prefix in _CONTENT_ICON_PREFIXES


# ==================================================
# JAR Icon 提取輔助函式（Phase 1: Model JSON 解析）
# ==================================================


def _get_icon_cache_dir() -> Path:
    """取得 icon 快取根目錄（統一至 .icon_cache/jar_icons/）。"""
    return get_data_root() / ".icon_cache" / "jar_icons"


def _migrate_old_icon_cache(source_root: Path) -> bool:
    """向後相容：將舊路徑的 icon cache 搬移至新路徑。

    舊路徑：source_root/_icon_preview/jar_icons/
    新路徑：.icon_cache/jar_icons/

    搬移條件：
        - 舊路徑存在
        - 新路徑尚不存在，或新路徑為空目錄

    回傳：
        True 表示有搬移，False 表示無需搬移
    """

    old_path = source_root / "_icon_preview" / "jar_icons"
    new_path = _get_icon_cache_dir()

    if not old_path.exists():
        # 舊路徑不存在，無需搬移
        return False

    # 新路徑已存在且有內容，不覆蓋
    if new_path.exists() and any(new_path.iterdir()):
        log_info(f"[IconPreview] 新 icon cache 已存在，放棄搬移舊路徑: {old_path}")
        return False

    # 確保新路徑的父目錄存在
    new_path.parent.mkdir(parents=True, exist_ok=True)

    # 搬移所有檔案
    files_moved = 0
    for old_file in old_path.glob("*.png"):
        new_file = new_path / old_file.name
        if not new_file.exists():
            shutil.move(str(old_file), str(new_file))
            files_moved += 1

    log_info(
        f"[IconPreview] 已將 {files_moved} 個 icon 檔案從舊路徑搬移至新路徑: {old_path} → {new_path}"
    )
    return True


def _resolve_icon_from_catalog(
    jar_path: Path,
    lang_namespace: str,
    key: str,
    catalog: ModpackAssetCatalog,
    *,
    current_zf: zipfile.ZipFile | None,
    budget: ZipReadBudget | None,
) -> IconHit | None:
    """解析 lang key 對應的圖示（``(texture, png_path, archive)``，附 ``.meta`` 預覽提示）。

    挑選規則在 ``app.icon_resolve``；本函式保留為呼叫端與測試 patch 的穩定入口。
    """
    return resolve_icon(
        jar_path,
        lang_namespace,
        key,
        catalog,
        current_zf=current_zf,
        budget=budget,
    )


# ==================================================
# 批次 Icon 提取（每個 JAR 只開一次 ZIP）
# ==================================================


def _apply_icon_index(jar_to_entries: dict[str, list], icon_index, progress_cb) -> int:
    """索引存在：直接用索引套用 icon，完全不做 JAR I/O。回傳處理的 JAR 數量。"""
    applied = 0
    for entries in jar_to_entries.values():
        for e in entries:
            raise_if_cancelled()
            if not (hasattr(e, "modid") and hasattr(e, "key")):
                continue
            key = e.key
            if key in icon_index:
                e.icon_path = icon_index[key]
                applied += 1
    if progress_cb:
        progress_cb(len(jar_to_entries), len(jar_to_entries))
    log_info(f"[IconPreview] 索引命中：{applied} 個 entry 直接套用 icon")
    return len(jar_to_entries)


def _run_jar_workers(jar_to_entries: dict[str, list], process_jar, progress_cb) -> int:
    """以 ThreadPoolExecutor 並行處理每個 JAR，並把結果套用到對應的 entry。回傳已處理的 JAR 數量。"""
    processed = 0
    total = len(jar_to_entries)
    max_workers = resolve_worker_count("icon_cache", load_config())

    def submit_jar(executor, jar_name: str):
        return executor.submit(process_jar, jar_name)

    with (
        ContextThreadPoolExecutor(max_workers=max_workers) as executor,
        bounded_as_completed(
            executor,
            jar_to_entries,
            submit_jar,
            max_in_flight=max_workers * 2,
        ) as completed,
    ):
        for future, jar_name in completed:
            raise_if_cancelled()
            try:
                entry_icon_paths = future.result()
                for e in jar_to_entries.get(jar_name, []):
                    raise_if_cancelled()
                    if hasattr(e, "key") and e.key in entry_icon_paths:
                        uri = entry_icon_paths[e.key]
                        if uri:
                            e.icon_path = uri
            except Exception as exc:  # noqa: BLE001 - 單一 JAR 圖示解析失敗不中止整批，但要留下是哪個 JAR
                log_warning(
                    f"[IconPreview] {jar_name} 圖示解析失敗：{exc!r}", exc_info=True
                )
            processed += 1
            if progress_cb:
                progress_cb(processed, total)
                if processed % 50 == 0:
                    log_info(f"[IconPreview] 處理進度：{processed}/{total} JARs")
    return processed


def _batch_extract_jar_icons(
    jar_to_entries: dict[str, list],
    icon_cache_root: Path,
    source_root: Path,
    progress_cb=None,
) -> int:
    """批次處理多個 JAR 的 icon 提取（支援預建立索引 + ThreadPoolExecutor）。

    PR60 優化架構：
        1. 嘗試從預建立索引讀取（instant，零 JAR I/O）
        2. 若無索引：使用 ThreadPoolExecutor 8 threads 建立索引（1-2 分鐘）
        3. 若索引正在建立中（另一執行緒）：降級為 ThreadPoolExecutor 即時處理

    參數：
        jar_to_entries: {jar_name: [entries]}，同一個 JAR 的所有 entry
        icon_cache_root: （已廢棄，參數保留但不再使用）
        source_root: 資料根目錄（JAR 所在位置）
        progress_cb: 進度回呼（可選）

    回傳：
        處理的 JAR 數量
    """

    # ===== Phase 0: Per-batch in-memory cache：同一 (modid, key) 在同一批次內不重複解析 =====
    _result_cache: dict[tuple[str, str], str | None] = {}

    # ===== Phase 1: 嘗試從預建立索引讀取（instant）=====
    icon_index = None
    try:
        from app import icon_index as idx_module

        icon_index = idx_module.load_icon_index(source_root)
    except Exception as exc:  # noqa: BLE001 - 預建索引載入失敗時改為逐 JAR 解析，但要留下紀錄
        log_warning(f"[IconPreview] 載入預建圖示索引失敗，改為逐 JAR 解析：{exc!r}")

    if icon_index is not None:
        return _apply_icon_index(jar_to_entries, icon_index, progress_cb)

    asset_catalog = ModpackAssetCatalog.from_mods_directory(source_root)

    # ===== Phase 2: 無索引 → ThreadPoolExecutor 即時處理 =====
    log_info(
        f"[IconPreview] 無索引，啟動 ThreadPoolExecutor 處理 {len(jar_to_entries)} 個 JAR"
    )

    def _process_jar(jar_name: str) -> dict[str, str | None]:
        """Worker：處理單一 JAR，回傳 {key: icon_uri or None}。"""
        jar_path = source_root / jar_name
        result_map: dict[str, str | None] = {}
        raise_if_cancelled()
        if not jar_path.exists():
            return result_map
        try:
            with zipfile.ZipFile(jar_path, "r") as zf:
                budget = ZipReadBudget.for_icon_scan(jar_name)
                for e in jar_to_entries.get(jar_name, []):
                    raise_if_cancelled()
                    if not (hasattr(e, "modid") and hasattr(e, "key")):
                        continue
                    modid = e.modid
                    key = e.key
                    # 預先過濾：不需要 icon 的 key 直接跳過，不浪費 lookup 時間
                    if not _key_needs_icon(key):
                        continue
                    # Per-(modid, key) cache：同 (modid, key) 在同批次不重複解析
                    cache_key = (modid, key)
                    if cache_key in _result_cache:
                        result_map[key] = _result_cache[cache_key]
                        continue
                    res = _resolve_icon_from_catalog(
                        jar_path,
                        modid,
                        key,
                        asset_catalog,
                        current_zf=zf,
                        budget=budget,
                    )
                    if res:
                        uri = hit_to_uri(res)
                        result_map[key] = uri
                        _result_cache[cache_key] = uri
                    else:
                        _result_cache[cache_key] = None
                        result_map[key] = None
        except ArchiveBudgetError:
            # 累計讀取超過安全上限（budget 已記錄警告）：保留已解析的部分，略過剩餘項目
            log_warning(
                f"[IconPreview] 略過 {jar_name} 剩餘項目的圖示解析（累計讀取超限）"
            )
        except Exception as exc:  # noqa: BLE001 - 單一 JAR 解析失敗保留已解析的部分，但要留下是哪個 JAR
            log_warning(
                f"[IconPreview] {jar_name} 圖示解析中斷：{exc!r}", exc_info=True
            )
        return result_map

    try:
        processed = _run_jar_workers(jar_to_entries, _process_jar, progress_cb)
    finally:
        asset_catalog.close()

    log_info(f"[IconPreview] ThreadPoolExecutor 完成：{processed} 個 JAR")
    return processed


# ==================================================
# L2 磁碟快取工具函式
# ==================================================


# ==================================================
# Phase 進度條輔助
# ==================================================


def to_halfwidth(text):
    """
    將字串正規化為半形（NFKC）
    - 只處理 str
    - 非 str 原樣返回（安全）
    """
    if not isinstance(text, str):
        return text
    return unicodedata.normalize("NFKC", text)
