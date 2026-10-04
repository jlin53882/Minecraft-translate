"""程式資源與可寫資料位置的單一決定點（#137）。

兩種根目錄：

* **資源根目錄** ``get_resource_root()``：隨程式附帶、唯讀的檔案（assets、
  config.example.json、resource_pack_version.json、pyproject.toml）。
* **資料根目錄** ``get_data_root()``：使用者可寫的資料（config.json、replace_rules.json、
  快取資料/、學名資料庫/、logs/、.icon_cache/…）。

打包後的目錄結構有兩種，由 exe 所在資料夾的名稱決定：

    分離式（建議）— exe 所在資料夾名稱為 ``app``     平面式（舊版相容）
    <根>/                                             <資料夾>/
     ├─ MinecraftTranslator.bat  ← 啟動器              ├─ MinecraftTranslator.exe
     ├─ app/        ← 整個換掉                         ├─ config.json、logs/ …
     │   └─ MinecraftTranslator.exe                    └─ 程式檔與資料混在一起
     └─ data/       ← 永遠保留
         └─ config.json、logs/、快取資料/ …

資料根目錄決定順序：
1. 環境變數 ``MCT_DATA_DIR``：明確覆蓋，供測試與特殊部署使用。
2. 打包後且 exe 位於名為 ``app`` 的資料夾：``<app 的上一層>/data``。
   首次啟動時會一次性把舊版遺留在 ``<app 的上一層>`` 的使用者資料搬進 ``data/``
   （只搬已知項目、不覆蓋 ``data/`` 內既有檔案、失敗的項目留在原處下次再試）。
3. 打包後但 exe 不在 ``app`` 資料夾：exe 所在資料夾（平面式，與舊版行為相同）。
4. 原始碼執行：專案根目錄（行為與過去相同）。

不要用 ``Path(__file__)`` 推算位置：打包後 ``__file__`` 可能指向 ``_internal/`` 或暫存
解壓目錄，不等於使用者看到的 exe 資料夾。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR_ENV = "MCT_DATA_DIR"
APP_DIR_NAME = "app"
DATA_DIR_NAME = "data"

# 舊版（平面式）會放在 exe 旁邊的使用者資料；分離式首次啟動時搬進 data/。
# 輸出資料夾不在此列：它們是相對於「使用者選的輸入資料夾」，不在 exe 資料夾內。
LEGACY_USER_ITEMS: tuple[str, ...] = (
    "config.json",
    "replace_rules.json",
    "logs",
    "快取資料",
    "學名資料庫",
    ".icon_cache",
    "custom_translators",
)

_migration_lock = threading.Lock()
_migrated_roots: set[Path] = set()


def _is_pyinstaller() -> bool:
    return bool(getattr(sys, "frozen", False))


def _is_nuitka() -> bool:
    # Nuitka 會在每個編譯後模組注入 `__compiled__` 全域變數。
    return "__compiled__" in globals()


def is_frozen() -> bool:
    """是否為打包後的執行檔。"""
    return _is_pyinstaller() or _is_nuitka()


def _source_root() -> Path:
    # translation_tool/utils/app_paths.py → parents[2] = 專案根目錄
    return Path(__file__).resolve().parents[2]


def get_exe_dir() -> Path | None:
    """打包後 exe 所在資料夾；原始碼執行時回傳 None。"""
    if _is_pyinstaller():
        return Path(sys.executable).resolve().parent
    if _is_nuitka():
        # onefile 時 __file__ 在暫存目錄；argv[0] 才是使用者啟動的 exe。
        return Path(sys.argv[0]).resolve().parent
    return None


def is_split_layout() -> bool:
    """打包後且 exe 位於名為 ``app`` 的資料夾（分離式）。"""
    exe_dir = get_exe_dir()
    return exe_dir is not None and exe_dir.name == APP_DIR_NAME


def get_resource_root() -> Path:
    """隨程式附帶的唯讀資源所在目錄。

    打包後：exe 所在資料夾（Nuitka 的 ``--include-data-*`` 會放在這裡）。
    原始碼執行：專案根目錄。
    """
    exe_dir = get_exe_dir()
    return exe_dir if exe_dir is not None else _source_root()


def get_data_root() -> Path:
    """回傳可寫資料的根目錄（config.json、快取、logs 等的基準）。"""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).resolve()
    exe_dir = get_exe_dir()
    if exe_dir is None:
        return _source_root()
    if exe_dir.name == APP_DIR_NAME:
        root = exe_dir.parent
        data_root = root / DATA_DIR_NAME
        _migrate_once(root, data_root)
        return data_root
    return exe_dir


def migrate_legacy_data(legacy_root: Path, data_root: Path) -> list[str]:
    """把舊版平面式遺留在 ``legacy_root`` 的使用者資料搬進 ``data_root``。

    - 只搬 ``LEGACY_USER_ITEMS`` 內的項目。
    - ``data_root`` 內已有同名項目時不覆蓋、不合併（避免洗掉較新的資料），留在原處。
    - 單一項目搬移失敗只記錄，不影響其他項目，下次啟動會再試。
    - 回傳實際搬移成功的項目名稱。
    """
    moved: list[str] = []
    for name in LEGACY_USER_ITEMS:
        src = legacy_root / name
        if not src.exists() and not src.is_symlink():
            continue
        dest = data_root / name
        if dest.exists():
            log.warning("略過搬移 %s：%s 已存在，保留舊位置的檔案", name, dest)
            continue
        try:
            data_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            moved.append(name)
        except OSError:
            log.warning(
                "搬移舊版資料 %s 到 %s 失敗，下次啟動再試", name, dest, exc_info=True
            )
    if moved:
        log.info("已把舊版資料搬進 %s：%s", data_root, ", ".join(moved))
    return moved


def _migrate_once(root: Path, data_root: Path) -> None:
    with _migration_lock:
        if root in _migrated_roots:
            return
        _migrated_roots.add(root)
    try:
        migrate_legacy_data(root, data_root)
    except Exception:  # 遷移失敗不可讓程式無法啟動
        log.warning("舊版資料遷移發生未預期錯誤", exc_info=True)
