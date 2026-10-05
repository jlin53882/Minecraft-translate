"""translation_db.py

唯讀的「預翻譯資料庫」查詢層（SQLite）。

資料庫結構（mod / mod_key / lang / source）由外部匯入工具產生；本模組只讀取，
不寫入、不建立資料表。翻譯流程在 cache 未命中、送 LLM 之前，可先以
``(mod_id, key)`` 查詢既有譯文；**原文必須與資料庫中的 en_us 完全一致**才採用，
避免模組更新原文後仍套用舊譯文。

設計原則：
- 預設關閉；路徑不存在或資料庫無法開啟時靜默停用，不影響翻譯流程
- 以模組為單位一次載入並快取，避免逐筆查詢
- 不碰 cache 與設定檔結構，設定由呼叫端傳入
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# source.s_num 的採用優先序：町宮字幕組 > 自訂補充 > 模組自帶繁中 > i18n 轉換
# > 簡中 CC 轉換 > AI 機翻。已校驗（checker > 0）的譯文永遠優先。
DEFAULT_SOURCE_PRIORITY: tuple[int, ...] = (3, 5, 1, 4, 2, 0)


def mod_id_from_path(file_path: str | Path) -> str | None:
    """由 ``.../assets/<mod_id>/lang/xx.json`` 取出 mod id；不符合時回傳 None。"""
    parts = Path(file_path).parts
    for idx, part in enumerate(parts[:-1]):
        if part == "assets" and idx + 1 < len(parts) - 1:
            return parts[idx + 1]
    return None


class TranslationDB:
    """預翻譯資料庫的唯讀查詢物件（thread-safe）。"""

    def __init__(
        self,
        db_path: str | Path,
        source_priority: tuple[int, ...] = DEFAULT_SOURCE_PRIORITY,
    ) -> None:
        self._path = Path(db_path)
        self._rank = {s: i for i, s in enumerate(source_priority)}
        self._lock = threading.Lock()
        self._mods: dict[str, dict[str, tuple[str, str]]] = {}
        self._conn: sqlite3.Connection | None = None
        self.available = False
        self._open()

    def _open(self) -> None:
        if not self._path.is_file():
            logger.warning("預翻譯資料庫不存在，已略過：%s", self._path)
            return
        try:
            uri = f"{self._path.resolve().as_uri()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._conn.execute("SELECT 1 FROM mod_key LIMIT 1")
            self.available = True
        except sqlite3.Error as exc:
            logger.warning("預翻譯資料庫無法開啟，已略過：%s（%s）", self._path, exc)
            self.close()

    def close(self) -> None:
        """關閉連線並停用。"""
        if self._conn is not None:
            self._conn.close()
        self._conn = None
        self.available = False

    def _load_mod(self, mod_id: str) -> dict[str, tuple[str, str]]:
        """載入單一模組：回傳 ``{key: (en_us, zh_tw)}``（已依優先序選出最佳譯文）。"""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT mk.key_val, mk.en_us, l.zh_tw, l.s_s_num, l.checker "
            "FROM mod m JOIN mod_key mk ON mk.m_s_num = m.s_num "
            "JOIN lang l ON l.mk_s_num = mk.s_num "
            "WHERE m.name = ? AND l.zh_tw IS NOT NULL AND l.zh_tw != ''",
            (mod_id,),
        ).fetchall()
        best: dict[str, tuple[tuple[int, int], str, str]] = {}
        for key, en_us, zh_tw, source, checker in rows:
            score = (0 if (checker or 0) > 0 else 1, self._rank.get(source, 99))
            cur = best.get(key)
            if cur is None or score < cur[0]:
                best[key] = (score, en_us or "", zh_tw)
        return {k: (en, tw) for k, (_, en, tw) in best.items()}

    def lookup(self, mod_id: str, key: str, source_text: str) -> str | None:
        """查詢譯文；原文與資料庫 en_us 不一致或查無資料時回傳 None。"""
        if not self.available or not source_text:
            return None
        with self._lock:
            table = self._mods.get(mod_id)
            if table is None:
                try:
                    table = self._load_mod(mod_id)
                except sqlite3.Error as exc:
                    logger.warning("預翻譯資料庫查詢失敗：%s（%s）", mod_id, exc)
                    self.close()
                    return None
                self._mods[mod_id] = table
        entry = table.get(key)
        if entry is None or entry[0] != source_text:
            return None
        return entry[1]


def split_items_by_translation_db(
    db: TranslationDB | None,
    items: list[dict[str, Any]],
    *,
    is_translated: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把 lang 類項目拆成「資料庫命中」與「未命中」。

    命中項目的 ``text`` 會換成譯文（與 cache 命中的格式一致）。``db`` 為 None 或
    不可用時，全部視為未命中。
    """
    if db is None or not db.available:
        return [], items
    hits: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for item in items:
        dst = None
        if (item.get("cache_type") or "lang") == "lang":
            mod_id = mod_id_from_path(item.get("file") or "")
            source = item.get("source_text") or item.get("text") or ""
            if mod_id:
                dst = db.lookup(mod_id, str(item.get("path") or ""), source)
        if dst and (is_translated is None or is_translated(dst)):
            new_item = dict(item)
            new_item["text"] = dst
            hits.append(new_item)
        else:
            rest.append(item)
    return hits, rest
