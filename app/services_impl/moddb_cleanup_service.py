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
    "revert_cleanup",
]

REPORT_DIR_NAME = "cleanup_reports"


def cleanup_report_dir(db: TranslationDB) -> Path:
    return db.path.parent / REPORT_DIR_NAME


def export_cleanup(
    db: TranslationDB, version: str, *, with_translation: bool = False
) -> CleanupReport:
    """把可清理的條目寫成檢查檔（只讀資料庫）。

    ``with_translation=True`` 改匯出「符合規則但已有譯文或歷史」的條目，
    檔名多一個 ``-translated`` 後綴。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_version = "".join(c if c.isalnum() or c in "._-" else "_" for c in version)
    if with_translation:
        path = (
            cleanup_report_dir(db) / f"cleanup-{safe_version}-{stamp}-translated.json"
        )
        return db.export_protected_report(version, path)
    path = cleanup_report_dir(db) / f"cleanup-{safe_version}-{stamp}.json"
    return db.export_cleanup_report(version, path)


def list_cleanup_reports(db: TranslationDB) -> list[Path]:
    """檢查檔清單，最新的在前。"""
    folder = cleanup_report_dir(db)
    if not folder.is_dir():
        return []
    return sorted(folder.glob("cleanup-*.json"), key=lambda p: p.name, reverse=True)


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
