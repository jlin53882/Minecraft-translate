"""資料清理與重複鍵清理的確定進度：done 單調遞增、結尾補滿，且不影響結果。"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from app.services_impl import moddb_cleanup_service as svc
from app.views.moddb.progress_reporter import (
    progress_text,
    progress_value,
    throttled_progress,
)
from translation_tool.translation_db import ScanItem, TranslationDB
from translation_tool.translation_db.operation_progress import OperationProgress
from translation_tool.translation_db.schema import SRC_MANUAL

VERSION = "1.21.1"
JUNK = 700  # 超過每 256 筆回報一次的間隔，才看得出節流與單調遞增
PROTECTED = 40
ENGLISH = "After harvesting dragon blood by using a glass."


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def __call__(self, done: int, total: int) -> None:
        self.calls.append((done, total))

    def assert_sane(self) -> None:
        assert self.calls, "至少要回報一次"
        dones = [d for d, _ in self.calls]
        totals = [t for _, t in self.calls]
        assert dones == sorted(dones), "done 必須單調遞增"
        assert totals == sorted(totals), "total 只會在新階段登記時增加"
        assert all(0 <= d <= t for d, t in self.calls)
        done, total = self.calls[-1]
        assert total > 0 and done == total, "最後一次回報必須是 done == total"


def _cleanup_db(path: Path) -> TranslationDB:
    db = TranslationDB(path)
    items = [
        ScanItem("lang", "m", f"item.m.todo{i}", "TODO: translate me")
        for i in range(JUNK)
    ]
    items += [
        ScanItem("lang", "m", f"item.m.prot{i}", "TODO: translate me", zh_tw=f"譯{i}")
        for i in range(PROTECTED)
    ]
    items += [ScanItem("lang", "m", f"item.m.ok{i}", "Alpha") for i in range(50)]
    db.ingest(VERSION, items)
    ids = [
        row[0]
        for row in db._q("SELECT id FROM entry WHERE key LIKE 'item.m.prot%' LIMIT 5")
    ]
    for entry_id in ids:
        db.save_manual(entry_id, "人工譯文")
    return db


def _plan_fields(plan) -> dict:
    data = dataclasses.asdict(plan)
    data.pop("batch_id", None)
    data.pop("token", None)  # 檢查檔內容（建立時間、資料庫身分）不同，token 本來就不同
    return data


def test_export_preview_apply_revert_report_monotonic_progress(tmp_path):
    db = _cleanup_db(tmp_path / "a.db")
    reference = _cleanup_db(tmp_path / "b.db")
    try:
        export, preview, apply_, revert = (Recorder() for _ in range(4))
        report = db.export_cleanup_report(VERSION, tmp_path / "r.json", progress=export)
        plain = reference.export_cleanup_report(VERSION, tmp_path / "r2.json")
        export.assert_sane()
        assert (report.counts, report.protected, report.default_selected) == (
            plain.counts,
            plain.protected,
            plain.default_selected,
        )
        assert export.calls[0][0] == 0 and export.calls[-1][1] >= JUNK

        previewed = db.preview_cleanup(tmp_path / "r.json", progress=preview)
        preview.assert_sane()
        assert _plan_fields(previewed) == _plan_fields(
            reference.preview_cleanup(tmp_path / "r2.json")
        )
        assert preview.calls[-1][1] == 3 * previewed.deletable

        applied = db.apply_cleanup(
            tmp_path / "r.json", previewed.token, progress=apply_
        )
        apply_.assert_sane()
        reference_plan = reference.apply_cleanup(
            tmp_path / "r2.json", reference.preview_cleanup(tmp_path / "r2.json").token
        )
        assert _plan_fields(applied) == _plan_fields(reference_plan)
        assert db._one("SELECT COUNT(*) FROM entry") == reference._one(
            "SELECT COUNT(*) FROM entry"
        )

        result = db.revert_cleanup(applied.batch_id, progress=revert)
        revert.assert_sane()
        assert result == reference.revert_cleanup(reference_plan.batch_id)
        assert db._one("SELECT COUNT(*) FROM entry") == (JUNK + PROTECTED + 50,)
    finally:
        db.close()
        reference.close()


def test_protected_export_and_protected_apply_revert_report_progress(tmp_path):
    db = _cleanup_db(tmp_path / "p.db")
    try:
        export, apply_, revert = Recorder(), Recorder(), Recorder()
        report = svc.export_cleanup(db, VERSION, with_translation=True, progress=export)
        export.assert_sane()
        assert sum(report.counts.values()) == PROTECTED
        plan = db.preview_cleanup(report.path)
        # 預設不刪的項目（有人工中文）不會被計入；其餘的有譯文項目會一起刪
        applied = svc.apply_cleanup(db, report.path, plan.token, progress=apply_)
        apply_.assert_sane()
        assert applied.with_translation == applied.deletable > 0
        restored = svc.revert_cleanup(db, applied.batch_id, progress=revert)
        revert.assert_sane()
        assert restored.complete and restored.restored == applied.deletable
    finally:
        db.close()


def test_cleanup_candidates_progress_is_optional_and_cached_scan_skips_scan_phase(
    tmp_path,
):
    db = _cleanup_db(tmp_path / "c.db")
    try:
        first, second = Recorder(), Recorder()
        candidates, skipped = db.cleanup_candidates(VERSION, progress=first)
        first.assert_sane()
        assert first.calls[-1][1] >= JUNK  # 第一次要真的掃描全部條目
        again, skipped_again = db.cleanup_candidates(VERSION, progress=second)
        second.assert_sane()
        assert (
            second.calls[-1][1] < first.calls[-1][1]
        )  # 命中快取：少了掃描條目的工作量
        assert [c.entry_id for c in again] == [c.entry_id for c in candidates]
        assert skipped_again == skipped
        plain, _ = db.cleanup_candidates(VERSION)
        assert [c.entry_id for c in plain] == [c.entry_id for c in candidates]
    finally:
        db.close()


def _insert_alias(db: TranslationDB, key: str, translation: str = "") -> int:
    with db._tx() as conn:
        entry_id = conn.execute(
            "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
            "VALUES ('guide', ?, 'bestiary', ?, ?)",
            (VERSION, key, ENGLISH),
        ).lastrowid
        if translation:
            conn.execute(
                "INSERT INTO translation (entry_id, source, zh_tw, review_status) "
                "VALUES (?, ?, ?, 'reviewed')",
                (entry_id, SRC_MANUAL, translation),
            )
        db._refresh(conn, [entry_id])
    return int(entry_id)


def _duplicate_db(path: Path, pairs: int = 30) -> TranslationDB:
    db = TranslationDB(path)
    for i in range(pairs):
        _insert_alias(db, f"lang/bestiary/_en_us/page_{i}.txt#0")
        _insert_alias(db, f"lang/bestiary/page_{i}.txt#0", translation=f"龍血{i}")
    return db


def test_duplicate_preview_apply_revert_report_monotonic_progress(tmp_path):
    db = _duplicate_db(tmp_path / "d.db")
    reference = _duplicate_db(tmp_path / "e.db")
    try:
        preview, apply_, revert = Recorder(), Recorder(), Recorder()
        plan = svc.preview_duplicate_cleanup(db, VERSION, progress=preview)
        preview.assert_sane()
        expected = svc.preview_duplicate_cleanup(reference, VERSION)
        assert _plan_fields(plan) == _plan_fields(expected)
        assert preview.calls[-1][1] >= 3 * 60 + 60  # 三輪查詢 + 每個候選的指紋

        applied = svc.apply_duplicate_cleanup(db, VERSION, plan.token, progress=apply_)
        apply_.assert_sane()
        applied_ref = svc.apply_duplicate_cleanup(reference, VERSION, expected.token)
        assert _plan_fields(applied) == _plan_fields(applied_ref)

        result = svc.revert_duplicate_cleanup(db, applied.batch_id, progress=revert)
        revert.assert_sane()
        assert revert.calls[-1] == (30, 30)
        assert result == svc.revert_duplicate_cleanup(reference, applied_ref.batch_id)
        assert db._one("SELECT COUNT(*) FROM entry") == (60,)
    finally:
        db.close()
        reference.close()


def test_progress_callback_may_be_none_everywhere(tmp_path):
    db = _duplicate_db(tmp_path / "n.db", pairs=3)
    try:
        plan = db.preview_duplicate_cleanup(VERSION, progress=None)
        applied = db.apply_duplicate_cleanup(VERSION, plan.token, progress=None)
        assert db.revert_duplicate_cleanup(applied.batch_id, progress=None).complete
    finally:
        db.close()


def test_operation_progress_pads_unprocessed_units_and_never_exceeds_total():
    recorder = Recorder()
    tracker = OperationProgress(recorder)
    tracker.expect(1000)
    with tracker.phase(600):
        tracker.tick(100)  # 其餘 500 筆已不存在：階段結束時補算
    assert tracker.done == 600
    with tracker.phase(400):
        tracker.tick(900)  # 超報也不超過總數
    tracker.finish()
    recorder.assert_sane()
    assert recorder.calls[0] == (0, 1000) and recorder.calls[-1] == (1000, 1000)
    disabled = OperationProgress(None)
    disabled.expect(5)
    disabled.tick(5)
    disabled.finish()
    assert (disabled.done, disabled.total) == (0, 0)


def test_throttled_progress_always_reports_first_and_last():
    now = [0.0]
    seen: list[tuple[int, int]] = []
    report = throttled_progress(
        lambda done, total: seen.append((done, total)),
        interval=0.1,
        clock=lambda: now[0],
    )
    report(0, 100)
    for done in range(1, 100):
        now[0] += 0.001  # 比節流間隔快很多
        report(done, 100)
    now[0] += 0.001
    report(100, 100)
    assert seen[0] == (0, 100) and seen[-1] == (100, 100)
    assert len(seen) < 20
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_progress_text_and_value_formatting():
    assert progress_text("預覽", 12000, 184000) == "預覽…已處理 12,000 / 184,000 筆"
    assert progress_text("預覽", 0, 0) == "預覽…"
    assert progress_value(50, 100) == 0.5
    assert progress_value(5, 0) is None
