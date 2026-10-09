"""translation_tool/core/lm_translator_db.py 模組。

用途：目錄翻譯與 Mod 翻譯資料庫之間的銜接——查詢（資料庫 → 快取 → AI 的第一站）與
翻譯結果寫回。資料庫不存在、未啟用或沒有指定版本時，所有函式都等同不存在，翻譯流程不受影響。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from translation_tool.core.lm_config_rules import value_fully_translated
from translation_tool.translation_db import (
    DbSettings,
    TranslationDB,
    TranslationResolver,
    WriteBackBuffer,
    load_db_settings,
    open_db,
    split_items_by_db,
)
from translation_tool.utils.log_unit import log_info


@dataclass
class DirectoryDbContext:
    """一次目錄翻譯使用的資料庫狀態。"""

    db: TranslationDB
    version: str
    resolver: TranslationResolver
    buffer: WriteBackBuffer | None
    root: Path

    def close(self) -> None:
        self.db.close()


def resolve_db_choice(
    use_db: bool | None = None,
    version: str | None = None,
    *,
    settings_snapshot=None,
) -> tuple[bool, str]:
    """這次任務**實際生效**的 (是否使用資料庫, 目標版本)。

    None 代表「用設定檔的值」；續跑用的 checkpoint 必須保存解析後的值，
    否則重開後設定檔改了，剩餘項目會查詢／寫回到不同的版本。
    """
    settings = settings_snapshot or load_db_settings()
    enabled = settings.enabled if use_db is None else bool(use_db)
    target = (version if version is not None else settings.version).strip()
    if enabled and not target:
        log_info(
            "📚 Mod 資料庫已啟用但尚未指定目標版本，已略過（請到設定或機器翻譯頁選擇）"
        )
    # 沒有版本或停用＝實際上不使用資料庫；回傳值就是這次任務的不可變選擇
    return (True, target) if enabled and target else (False, "")


def get_db_settings_snapshot(settings_snapshot: DbSettings | None = None) -> DbSettings:
    """Return one settings snapshot through this module's established seam."""
    return settings_snapshot or load_db_settings()


def open_directory_db(
    root: str | Path,
    *,
    use_db: bool | None = None,
    version: str | None = None,
    settings_snapshot=None,
) -> DirectoryDbContext | None:
    """依設定開啟資料庫；``use_db`` / ``version`` 為 None 時使用設定檔的值。"""
    settings = settings_snapshot or load_db_settings()
    enabled = settings.enabled if use_db is None else use_db
    if not enabled:
        return None
    target = (version if version is not None else settings.version).strip()
    if not target:
        log_info(
            "📚 Mod 資料庫已啟用但尚未指定目標版本，已略過（請到設定或機器翻譯頁選擇）"
        )
        return None
    # LM 讀寫需要可寫連線，但這是任務快照，不可在開啟時覆寫資料庫共用 priority。
    db = open_db(settings, create=False, sync_priority=False)
    if db is None:
        return None
    expected_path = settings.resolved_path().resolve()
    if db.path.resolve() != expected_path:
        log_info(
            "📚 Mod 資料庫路徑與本次操作快照不一致，略過資料庫："
            f"預期 {expected_path}，實際 {db.path.resolve()}"
        )
        db.close()
        return None
    resolver = TranslationResolver(
        db,
        target,
        cross_version=settings.cross_version,
        source_priority=settings.priority,
    )
    buffer = (
        WriteBackBuffer(db, target, root, fill_other_versions=True)
        if settings.write_back
        else None
    )
    log_info(f"📚 使用 Mod 資料庫：{db.path.name}（目標版本 {target}）")
    return DirectoryDbContext(db, target, resolver, buffer, Path(root))


@contextmanager
def directory_db(
    root: str | Path,
    *,
    use_db: bool | None = None,
    version: str | None = None,
    settings_snapshot=None,
) -> Iterator[DirectoryDbContext | None]:
    """``open_directory_db`` 的 context manager 版本：離開時一定關閉資料庫。"""
    ctx = open_directory_db(
        root,
        use_db=use_db,
        version=version,
        settings_snapshot=settings_snapshot,
    )
    try:
        yield ctx
    finally:
        if ctx is not None:
            ctx.close()


def split_db_hits(
    ctx: DirectoryDbContext | None, items: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """資料庫命中／未命中；命中的項目 ``text`` 換成資料庫譯文。並記錄統計日誌。"""
    if ctx is None:
        return [], items
    hits, rest = split_items_by_db(
        ctx.resolver, items, ctx.root, is_translated=value_fully_translated
    )
    stats = ctx.resolver.stats
    log_info(
        f"📚 預翻譯資料庫命中 {len(hits)} 筆"
        f"（目標版本 {stats.hit_target}　跨版本沿用 {stats.hit_cross}）"
    )
    if stats.en_mismatch:
        log_info(
            f"🔎 資料庫有相同鍵值但原文不同：{stats.en_mismatch} 筆（改走快取／AI）"
        )
    return hits, rest


def flush_write_back(ctx: DirectoryDbContext | None, label: str = "") -> None:
    """把累積的翻譯結果寫回資料庫並記錄日誌。"""
    if ctx is None or ctx.buffer is None:
        return
    done = ctx.buffer.flush()
    if done.written or done.filled_other:
        log_info(
            f"📚 {label}已寫入 Mod 資料庫 {done.written} 筆"
            + (
                f"（其他版本補上空白 {done.filled_other} 筆）"
                if done.filled_other
                else ""
            )
        )
