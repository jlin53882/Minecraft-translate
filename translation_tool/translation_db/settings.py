"""settings.py

讀取 ``translation_db`` 設定並開啟資料庫。翻譯流程與介面共用同一個入口，
資料庫缺檔或無法開啟時回傳 None（功能靜默停用，不影響其他流程）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.schema import (
    DB_FOREIGN,
    DB_NEWER,
    DEFAULT_PRIORITY,
    SOURCE_NAMES,
    classify_database,
)
from translation_tool.utils.log_unit import log_info, log_warning

DEFAULT_DB_FILE = "mod_translation.db"


@dataclass(frozen=True)
class DbSettings:
    """``translation_db`` 設定的快照。"""

    enabled: bool = True
    path: str = DEFAULT_DB_FILE
    version: str = ""
    cross_version: bool = True
    write_back: bool = True
    sync_manual: bool = True
    priority: tuple[int, ...] = DEFAULT_PRIORITY
    zip_source: int | None = None  # 翻譯 ZIP 匯入時，「譯文來源標記」的預設來源

    @property
    def usable(self) -> bool:
        """已啟用且指定了遊戲版本（沒有版本無法判斷寫回哪一版）。"""
        return self.enabled and bool(self.version.strip())

    def resolved_path(self) -> Path:
        from translation_tool.utils.config_manager import resolve_project_path

        return resolve_project_path(self.path or DEFAULT_DB_FILE)


def parse_priority(names: Any) -> tuple[int, ...]:
    """把來源名稱清單轉成代碼優先序；未列出的來源依預設順序接在後面。"""
    by_name = {name: code for code, name in SOURCE_NAMES.items()}
    order: list[int] = []
    for name in names or []:
        code = by_name.get(str(name).strip())
        if code is not None and code not in order:
            order.append(code)
    order.extend(c for c in DEFAULT_PRIORITY if c not in order)
    return tuple(order)


def priority_names(priority: tuple[int, ...]) -> list[str]:
    return [SOURCE_NAMES[c] for c in priority]


def load_db_settings(config: dict | None = None) -> DbSettings:
    """由設定檔建立 ``DbSettings``（缺欄位用預設）。"""
    if config is None:
        from translation_tool.utils.config_manager import load_config

        config = load_config()
    cfg = config.get("translation_db", {}) or {}
    return DbSettings(
        enabled=bool(cfg.get("enabled", True)),
        path=str(cfg.get("path") or DEFAULT_DB_FILE),
        version=str(cfg.get("version") or "").strip(),
        cross_version=bool(cfg.get("cross_version", True)),
        write_back=bool(cfg.get("write_back", True)),
        sync_manual=bool(cfg.get("sync_manual", True)),
        priority=parse_priority(cfg.get("priority")),
        zip_source={name: code for code, name in SOURCE_NAMES.items()}.get(
            str(cfg.get("zip_source") or "").strip()
        ),
    )


_open_lock = threading.Lock()


def database_problem(settings: DbSettings) -> str:
    """設定的資料庫檔案「存在但不能用」的原因（正常或尚未建立回傳空字串）。

    供介面顯示：開不起來時不能讓使用者看到「尚未建立資料庫」而去重新掃描
    （掃描一樣會被拒絕，還會誤以為是自己操作錯誤）。
    """
    path = settings.resolved_path()
    if not path.is_file():
        return ""
    kind = classify_database(path)
    if kind == DB_FOREIGN:
        return f"設定的檔案不是 Mod 翻譯資料庫（為避免污染不會使用）：{path}。請到設定頁改用其他路徑。"
    if kind == DB_NEWER:
        return f"資料庫版本比本程式新，請更新程式後再使用：{path}"
    return ""


def open_db(settings: DbSettings, *, create: bool = False) -> TranslationDB | None:
    """開啟設定指定的資料庫。

    ``create=False``（翻譯流程）：檔案不存在就回傳 None，不會憑空建立。
    ``create=True``（掃描／介面）：不存在則建立。
    """
    path = settings.resolved_path()
    with _open_lock:
        if not path.is_file() and not create:
            log_info(f"📚 預翻譯資料庫尚未建立（{path}），已略過")
            return None
        try:
            return TranslationDB(path, priority=settings.priority, create=create)
        except Exception as exc:  # noqa: BLE001 - 資料庫問題不應中斷翻譯
            log_warning(f"⚠️ 預翻譯資料庫無法開啟，已略過：{path}（{exc!r}）")
            return None
