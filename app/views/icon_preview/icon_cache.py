"""圖示預覽頁的圖示提取與快取輔助函式（由 icon_preview_view.py 拆出，#114）。

含舊 icon cache 目錄搬移、圖示解析入口與半形正規化。冷載入的逐 JAR 掃描在 ``app.icon_jar_scan``。
"""

import shutil
import unicodedata
import zipfile
from pathlib import Path

from app.icon_catalog import ModpackAssetCatalog
from app.icon_resolve import IconHit, key_needs_icon, resolve_icon
from translation_tool.utils.app_paths import get_data_root
from translation_tool.utils.log_unit import log_info
from translation_tool.utils.zip_safety import ZipReadBudget

_key_needs_icon = key_needs_icon


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
