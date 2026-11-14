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
from translation_tool.translation_db.operation_progress import (
    OperationProgress,
    PhaseProgress,
)
from translation_tool.translation_db.schema import SRC_MANUAL

VERSION = "1.21.1"
JUNK = 700  # 超過每 256 筆回報一次的間隔，才看得出節流與單調遞增
PROTECTED = 40
ENGLISH = "After harvesting dragon blood by using a glass."


class Recorder:
    def __init__(self) -> None:
        self.calls: list[PhaseProgress] = []

    def __call__(self, progress: PhaseProgress) -> None:
        self.calls.append(progress)

    def phases(self) -> list[int]:
        """依出現順序列出有回報的階段編號。"""
        seen: list[int] = []
        for call in self.calls:
            if not seen or seen[-1] != call.index:
                seen.append(call.index)
        return seen

    def assert_sane(self, expected_count: int) -> None:
        assert self.calls, "至少要回報一次"
        assert {c.count for c in self.calls} == {expected_count}, "階段數固定"
        phases = self.phases()
        assert phases == sorted(set(phases)), "階段編號只增不減"
        assert all(1 <= p <= expected_count for p in phases)
        for index in phases:
            part = [c for c in self.calls if c.index == index]
            assert len({c.total for c in part}) == 1, "階段內總數不變"
            assert len({c.label for c in part}) == 1 and part[0].label
            dones = [c.done for c in part]
            assert dones == sorted(dones), "階段內 done 必須單調遞增"
            assert dones[0] == 0 and dones[-1] == part[0].total, "每個階段從 0 到 total"


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
        export.assert_sane(2)
        assert (report.counts, report.protected, report.default_selected) == (
            plain.counts,
            plain.protected,
            plain.default_selected,
        )
        assert export.calls[0] == PhaseProgress(
            1, 2, "掃描條目", 0, export.calls[0].total
        )
        assert export.calls[0].total >= JUNK

        previewed = db.preview_cleanup(tmp_path / "r.json", progress=preview)
        preview.assert_sane(3)
        assert preview.phases() == [1, 2, 3]
        assert _plan_fields(previewed) == _plan_fields(
            reference.preview_cleanup(tmp_path / "r2.json")
        )
        assert {c.total for c in preview.calls} == {previewed.deletable}

        applied = db.apply_cleanup(
            tmp_path / "r.json", previewed.token, progress=apply_
        )
        apply_.assert_sane(5)
        assert apply_.phases() == [1, 2, 3, 4, 5]
        reference_plan = reference.apply_cleanup(
            tmp_path / "r2.json", reference.preview_cleanup(tmp_path / "r2.json").token
        )
        assert _plan_fields(applied) == _plan_fields(reference_plan)
        assert db._one("SELECT COUNT(*) FROM entry") == reference._one(
            "SELECT COUNT(*) FROM entry"
        )

        result = db.revert_cleanup(applied.batch_id, progress=revert)
        revert.assert_sane(5)
        assert revert.phases() == [1, 2, 3, 4, 5]
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
        export.assert_sane(4)
        assert export.phases() == [1, 2, 3, 4]
        assert sum(report.counts.values()) == PROTECTED
        plan = db.preview_cleanup(report.path)
        # 預設不刪的項目（有人工中文）不會被計入；其餘的有譯文項目會一起刪
        applied = svc.apply_cleanup(db, report.path, plan.token, progress=apply_)
        apply_.assert_sane(5)
        assert applied.with_translation == applied.deletable > 0
        restored = svc.revert_cleanup(db, applied.batch_id, progress=revert)
        revert.assert_sane(5)
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
        first.assert_sane(2)
        assert first.phases() == [1, 2]
        assert first.calls[0].total >= JUNK  # 第一次要真的掃描全部條目
        again, skipped_again = db.cleanup_candidates(VERSION, progress=second)
        second.assert_sane(2)
        # 命中快取：沒有掃描工作，直接從第 2 階段開始（略過的階段不顯示，編號不重排）
        assert second.phases() == [2]
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
        preview.assert_sane(2)
        assert preview.phases() == [1, 2]
        expected = svc.preview_duplicate_cleanup(reference, VERSION)
        assert _plan_fields(plan) == _plan_fields(expected)
        assert preview.calls[0].total == 3 * 60  # 第一階段：三輪查詢各讀範圍內全部條目
        assert preview.calls[-1].total == 60  # 第二階段：每個候選條目算指紋

        applied = svc.apply_duplicate_cleanup(db, VERSION, plan.token, progress=apply_)
        apply_.assert_sane(6)
        assert apply_.phases() == [1, 2, 3, 4, 5, 6]
        applied_ref = svc.apply_duplicate_cleanup(reference, VERSION, expected.token)
        assert _plan_fields(applied) == _plan_fields(applied_ref)

        result = svc.revert_duplicate_cleanup(db, applied.batch_id, progress=revert)
        revert.assert_sane(1)
        assert revert.calls[-1].done == revert.calls[-1].total == 30
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


def test_operation_progress_phases_are_independent_and_pad_to_total():
    recorder = Recorder()
    tracker = OperationProgress(recorder, ("第一", "第二", "第三"))
    with tracker.phase(1, 1000):
        tracker.tick(100)  # 其餘 900 筆已不存在：階段結束時補滿
    assert tracker.done == 1000
    # 第 2 階段沒有工作可做（例如快取命中），直接從第 3 階段開始
    with tracker.phase(3, 400):
        tracker.tick(900)  # 超報也不超過該階段的總數
    tracker.finish()
    recorder.assert_sane(3)
    assert recorder.phases() == [1, 3]
    assert recorder.calls[0] == PhaseProgress(1, 3, "第一", 0, 1000)
    assert recorder.calls[-1] == PhaseProgress(3, 3, "第三", 400, 400)
    disabled = OperationProgress(None, ("x",))
    disabled.begin(1, 5)
    disabled.tick(5)
    disabled.finish()
    assert (disabled.done, disabled.total) == (0, 0)


def test_throttled_progress_reports_phase_boundaries_and_throttles_inside():
    now = [0.0]
    seen: list[PhaseProgress] = []
    report = throttled_progress(seen.append, interval=0.1, clock=lambda: now[0])
    for index in (1, 2):
        report(PhaseProgress(index, 2, f"階段{index}", 0, 100))
        for done in range(1, 100):
            now[0] += 0.001  # 比節流間隔快很多
            report(PhaseProgress(index, 2, f"階段{index}", done, 100))
        now[0] += 0.001
        report(PhaseProgress(index, 2, f"階段{index}", 100, 100))
    assert [(c.index, c.done) for c in seen if c.done in (0, 100)] == [
        (1, 0),
        (1, 100),
        (2, 0),
        (2, 100),
    ]
    assert len(seen) < 40
    assert [(c.index, c.done) for c in seen] == sorted((c.index, c.done) for c in seen)


def test_progress_text_and_value_formatting():
    middle = PhaseProgress(2, 3, "讀取條目", 1200, 8000)
    assert progress_text(middle) == "第 2／3 階段：讀取條目 1,200 / 8,000 筆"
    assert progress_value(middle) == 0.15
    single = PhaseProgress(1, 1, "復原重複鍵合併", 5, 10)
    assert progress_text(single) == "復原重複鍵合併 5 / 10 筆"
    empty = PhaseProgress(2, 2, "檢查", 0, 0)
    assert progress_text(empty) == "第 2／2 階段：檢查 0 / 0 筆"
    assert progress_value(empty) == 1.0  # 沒有筆數要處理的階段視為已完成


def test_raising_progress_callback_does_not_break_operation(tmp_path):
    """進度回呼出錯只是看不到進度，不可讓寫入交易回滾。"""

    def boom(_progress: PhaseProgress) -> None:
        raise RuntimeError("ui gone")

    db = _duplicate_db(tmp_path / "boom.db", pairs=3)
    try:
        plan = db.preview_duplicate_cleanup(VERSION, progress=None)
        applied = db.apply_duplicate_cleanup(VERSION, plan.token, progress=boom)
        assert db.revert_duplicate_cleanup(applied.batch_id, progress=boom).complete
    finally:
        db.close()
