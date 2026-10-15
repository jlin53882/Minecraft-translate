"""資料庫清理的服務層（供 UI 使用）：匯出檢查檔、依檢查檔預覽與刪除、還原。

檢查檔存在資料庫檔案旁的 ``cleanup_reports`` 資料夾，使用者可以直接用編輯器開啟檢查；
View 不直接碰引擎，檔案位置與命名規則都在這裡。
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from app.services_impl.pipelines.extract_service import open_output_folder
from translation_tool.translation_db import (
    CLEANUP_CATEGORIES,
    CleanupBatchInfo,
    CleanupPlan,
    CleanupReport,
    TranslationDB,
)

__all__ = [
    "CLEANUP_CATEGORIES",
    "CleanupBatchInfo",
    "CleanupPlan",
    "CleanupReport",
    "apply_cleanup",
    "cleanup_overview",
    "cleanup_report_dir",
    "export_cleanup",
    "last_cleanup_batch",
    "list_cleanup_reports",
    "open_report_folder",
    "preview_cleanup",
    "report_file_count",
    "revert_cleanup",
    "translated_overview",
]

REPORT_DIR_NAME = "cleanup_reports"


def cleanup_report_dir(db: TranslationDB) -> Path:
    return db.path.parent / REPORT_DIR_NAME


def export_cleanup(
    db: TranslationDB, version: str, *, translated: bool = False
) -> CleanupReport:
    """把可清理的條目寫成檢查檔（只讀資料庫）。

    ``translated=True`` 匯出「已翻譯內容」（已有生效譯文的條目）：寫成依模組拆檔的資料夾，
    名稱多一個 ``-translated`` 後綴，清單仍依時間排序。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_version = "".join(c if c.isalnum() or c in "._-" else "_" for c in version)
    base = cleanup_report_dir(db) / f"cleanup-{safe_version}-{stamp}"
    if translated:
        return db.export_translated_report(version, f"{base}-translated")
    return db.export_cleanup_report(version, f"{base}.json")


def list_cleanup_reports(db: TranslationDB) -> list[Path]:
    """檢查檔與已翻譯內容的檢查檔資料夾，最新的在前。"""
    folder = cleanup_report_dir(db)
    if not folder.is_dir():
        return []
    reports = [
        path
        for path in folder.glob("cleanup-*")
        if (path.is_file() and path.suffix == ".json")
        or (path.is_dir() and path.name.endswith("-translated"))
    ]
    return sorted(reports, key=lambda p: p.name, reverse=True)


def report_file_count(path: Path) -> int:
    """檢查檔資料夾裡還有幾個模組檢查檔（單一檢查檔回傳 1）。"""
    return len(list(path.glob("*.json"))) if path.is_dir() else 1


def preview_cleanup(db: TranslationDB, path: Path) -> CleanupPlan:
    return db.preview_cleanup(path)


def apply_cleanup(db: TranslationDB, path: Path) -> CleanupPlan:
    return db.apply_cleanup(path)


def last_cleanup_batch(db: TranslationDB) -> CleanupBatchInfo | None:
    return db.last_cleanup_batch()


def revert_cleanup(db: TranslationDB, batch_id: str) -> tuple[int, int]:
    return db.revert_cleanup(batch_id)


def open_report_folder(db: TranslationDB) -> bool:
    """用檔案管理員開啟檢查檔資料夾（資料夾不存在時先建立；不等待外部程式）。"""
    folder = cleanup_report_dir(db)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return open_output_folder(str(folder))


def cleanup_overview(
    db: TranslationDB, version: str
) -> tuple[dict[tuple[str, str], int], int]:
    """所選版本可清理的筆數：``({(類型, 類別): 筆數}, 因有譯文或歷史而不列入的筆數)``（只讀）。"""
    candidates, protected = db.cleanup_candidates(version)
    counts = Counter((c.kind, c.category) for c in candidates)
    return dict(counts), sum(protected.values())


def translated_overview(db: TranslationDB, version: str) -> dict[str, int]:
    """所選版本已有生效譯文的條目數，依類型分（只讀；「已翻譯內容」可清理的筆數）。"""
    return db.translated_counts(version)
