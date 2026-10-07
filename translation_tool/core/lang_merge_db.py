"""translation_tool/core/lang_merge_db.py 模組。

用途：語系合併（階段 1／階段 2）以 Mod 資料庫為「額外的譯文來源」。

``merge_lang_dicts`` 判定為純英文、本來要進「待翻譯」的條目，先到資料庫找同
（模組、鍵值、原文）的既有譯文：命中就直接放進 ``zh_tw``、不再進待翻譯。
只補「本來沒有譯文」的條目——jar 自帶繁中、簡中轉繁、輸出資料夾既有（含人工）譯文
一律不被資料庫覆蓋。資料庫未啟用、沒指定版本或不存在時等同不存在，合併流程不變。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from translation_tool.core.lm_config_rules import value_fully_translated
from translation_tool.translation_db import (
    TranslationDB,
    TranslationResolver,
    load_db_settings,
    open_db,
)
from translation_tool.translation_db.schema import KIND_LANG
from translation_tool.utils.log_unit import log_debug, log_info


class MergeDbFill:
    """語系合併用的資料庫補譯器（可在合併的多執行緒中共用）。"""

    def __init__(self, db: TranslationDB, version: str, cross_version: bool) -> None:
        self.db = db
        self.version = version
        self.resolver = TranslationResolver(db, version, cross_version=cross_version)
        self._lock = threading.Lock()
        self.filled = 0

    def fill(
        self,
        mod_id: str | None,
        final_tw: dict[str, Any],
        pending: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], int]:
        """用資料庫譯文補 ``pending`` 內的條目；回傳 ``(final_tw, pending, 補上筆數)``。"""
        if not mod_id or not pending:
            return final_tw, pending, 0
        remaining: dict[str, Any] = {}
        hits = 0
        for key, en in pending.items():
            hit = (
                self.resolver.lookup(KIND_LANG, mod_id, key, en)
                if isinstance(en, str)
                else None
            )
            if hit and hit.zh_tw and value_fully_translated(hit.zh_tw):
                final_tw[key] = hit.zh_tw
                hits += 1
            else:
                remaining[key] = en
        if hits:
            with self._lock:
                self.filled += hits
        return final_tw, remaining, hits

    def close(self) -> None:
        self.db.close()


def open_merge_db_fill(
    use_db: bool | None = None, version: str | None = None
) -> MergeDbFill | None:
    """依設定開啟資料庫；``use_db`` / ``version`` 為 None 時用設定檔的值（與機器翻譯一致）。"""
    settings = load_db_settings()
    enabled = settings.merge_enabled if use_db is None else bool(use_db)
    if not enabled:
        return None
    target = (version if version is not None else settings.version).strip()
    if not target:
        log_debug("語系合併：Mod 資料庫已啟用但尚未指定目標版本，略過資料庫補譯")
        return None
    db = open_db(settings, create=False)
    if db is None:  # 資料庫尚未建立／無法開啟：略過（open_db 已記錄原因）
        return None
    log_info(f"📚 語系合併使用 Mod 資料庫補譯：{db.path.name}（目標版本 {target}）")
    return MergeDbFill(db, target, settings.cross_version)


@contextmanager
def merge_db_fill(
    use_db: bool | None = None, version: str | None = None
) -> Iterator[MergeDbFill | None]:
    """``open_merge_db_fill`` 的 context manager 版本：離開時一定關閉資料庫並記錄統計。"""
    fill = open_merge_db_fill(use_db, version)
    try:
        yield fill
    finally:
        if fill is not None:
            stats = fill.resolver.stats
            log_info(
                f"📚 語系合併由資料庫補上 {fill.filled} 筆譯文"
                f"（目標版本 {stats.hit_target}　跨版本沿用 {stats.hit_cross}）"
            )
            fill.close()
