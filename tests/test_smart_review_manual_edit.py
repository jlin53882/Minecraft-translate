"""智慧校對人工編輯覆蓋套用（含已套用重套）與快照對齊的測試。"""

from __future__ import annotations

import json

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    TranslationDB,
    WriteBackItem,
)
from translation_tool.translation_db.schema import SRC_MANUAL
from translation_tool.translation_db.smart_review import run_smart_review


@pytest.fixture
def db(tmp_path):
    """建立含兩筆 lang 條目的測試資料庫。"""
    database = TranslationDB(tmp_path / "manual-edit.db")
    database.write_back(
        "1.21.1",
        [
            WriteBackItem(KIND_LANG, "demo", "hello", "Hello %s", "你好 %s"),
            WriteBackItem(KIND_LANG, "demo", "bye", "Goodbye", "再見"),
        ],
        fill_other_versions=False,
    )
    yield database
    database.close()


def _run(db, monkeypatch, decision: str):
    """以固定決策執行智慧校對，回傳 run_id。"""

    def fake_call(**kwargs):
        """模擬模型：所有項目回同一個決策。"""
        items = []
        for item in kwargs["payload"]["items"]:
            entry = {
                "id": item["id"],
                "decision": decision,
                "suggested_zh_tw": "再見！" if decision == "suggest_revision" else "",
                "issue_types": [],
                "evidence_ids": [],
                "confidence": 0.8,
                "reason": "原文 Goodbye 與目前譯文 再見 的語氣需要人工確認。",
            }
            items.append(entry)
        return json.dumps({"items": items})

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    return run_smart_review(
        db, model_name="gemini-test", api_key="test-key", limit=2
    ).run_id


def _candidate(db, run_id: str, key: str):
    """依 key 取得候選。"""
    return next(c for c in db.list_smart_review_candidates(run_id) if c.key == key)


def _apply_history(db, entry_id: int):
    """列出條目的智慧校對套用歷史。"""
    return [
        row
        for row in db.entry_detail(entry_id).history
        if row.action == "smart_review_apply"
    ]


def test_no_change_candidate_can_be_edited_and_becomes_applied(db, monkeypatch):
    """「AI 認為不用改」的筆，人工編輯後套用並標為已套用。"""
    run_id = _run(db, monkeypatch, "no_change")
    candidate = _candidate(db, run_id, "bye")
    assert candidate.status == "no_change"

    assert db.realign_smart_review_candidate(run_id, candidate.ordinal) == "unchanged"
    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再會！")
        == "applied"
    )

    stored = _candidate(db, run_id, "bye")
    assert stored.status == "applied"
    assert stored.draft_zh_tw == "再會！"
    assert stored.applied_batch
    entry = db.entry_detail(candidate.entry_id).entry
    assert (entry.source, entry.zh_tw) == (SRC_MANUAL, "再會！")
    assert len(_apply_history(db, candidate.entry_id)) == 1


def test_applied_candidate_can_be_edited_and_reapplied(db, monkeypatch):
    """已套用的筆可再編輯重套，覆蓋同一個 manual 列並新增歷史，且可還原。"""
    run_id = _run(db, monkeypatch, "suggest_revision")
    candidate = _candidate(db, run_id, "bye")
    assert db.apply_smart_review_candidate(run_id, candidate.ordinal) == "applied"

    assert db.realign_smart_review_candidate(run_id, candidate.ordinal) == "realigned"
    realigned = _candidate(db, run_id, "bye")
    assert realigned.current_zh_tw == "再見！"
    assert realigned.status == "applied"

    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再見囉！")
        == "applied"
    )
    entry = db.entry_detail(candidate.entry_id).entry
    assert (entry.source, entry.zh_tw) == (SRC_MANUAL, "再見囉！")
    history = _apply_history(db, candidate.entry_id)
    assert len(history) == 2

    # 最新一筆還原後回到第一次套用的值，而不是破壞 manual 列。
    latest = max(history, key=lambda row: row.id)
    assert db.revert(latest.id, whole_batch=False) == 1
    assert db.entry_detail(candidate.entry_id).entry.zh_tw == "再見！"


def test_reapply_with_unchanged_text_writes_nothing(db, monkeypatch):
    """草稿與目前譯文相同時不寫入也不新增歷史。"""
    run_id = _run(db, monkeypatch, "suggest_revision")
    candidate = _candidate(db, run_id, "bye")
    assert db.apply_smart_review_candidate(run_id, candidate.ordinal) == "applied"
    db.realign_smart_review_candidate(run_id, candidate.ordinal)

    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再見！")
        == "unchanged"
    )
    assert len(_apply_history(db, candidate.entry_id)) == 1


def test_manual_edit_refuses_changed_snapshot_without_downgrading(db, monkeypatch):
    """資料庫在對齊後被他處修改時拒絕覆蓋，候選狀態維持不變；重新對齊後可套用。"""
    run_id = _run(db, monkeypatch, "needs_context")
    candidate = _candidate(db, run_id, "bye")
    assert candidate.status == "needs_context"
    db.realign_smart_review_candidate(run_id, candidate.ordinal)

    db.save_manual(candidate.entry_id, "掰掰", propagate=False)

    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再會！")
        == "changed"
    )
    assert _candidate(db, run_id, "bye").status == "needs_context"
    assert db.entry_detail(candidate.entry_id).entry.zh_tw == "掰掰"

    assert db.realign_smart_review_candidate(run_id, candidate.ordinal) == "realigned"
    assert _candidate(db, run_id, "bye").current_zh_tw == "掰掰"
    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再會！")
        == "applied"
    )


def test_stale_candidate_is_editable_after_realign(db, monkeypatch):
    """已過期的筆對齊現況後即可人工編輯套用。"""
    run_id = _run(db, monkeypatch, "suggest_revision")
    candidate = _candidate(db, run_id, "bye")
    db.save_manual(candidate.entry_id, "掰掰", propagate=False)
    assert db.apply_smart_review_candidate(run_id, candidate.ordinal) == "stale"
    assert _candidate(db, run_id, "bye").status == "stale"

    assert db.realign_smart_review_candidate(run_id, candidate.ordinal) == "realigned"
    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再會！")
        == "applied"
    )
    assert _candidate(db, run_id, "bye").status == "applied"


def test_manual_edit_rejects_bad_format_without_changing_status(db, monkeypatch):
    """格式不合格或空白的草稿被拒絕，候選不會被鎖成 invalid_format。"""
    run_id = _run(db, monkeypatch, "no_change")
    candidate = _candidate(db, run_id, "hello")

    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "你好")
        == "invalid_format"
    )
    assert db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "  ") == (
        "invalid_draft"
    )
    assert _candidate(db, run_id, "hello").status == "no_change"
    assert db.entry_detail(candidate.entry_id).entry.zh_tw == "你好 %s"


def test_manual_edit_does_not_apply_to_ready_candidates(db, monkeypatch):
    """可套用（ready）的筆仍走原本的 AI 建議流程，不能用人工編輯路徑繞過。"""
    run_id = _run(db, monkeypatch, "suggest_revision")
    candidate = _candidate(db, run_id, "bye")
    assert candidate.status == "ready"

    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再會！")
        == "not_editable"
    )
    assert (
        db.realign_smart_review_candidate(run_id, candidate.ordinal) == "not_editable"
    )
    assert db.entry_detail(candidate.entry_id).entry.zh_tw == "再見"


def test_manual_edit_keeps_reviewed_manual_translation(db, monkeypatch):
    """人工已審核過的譯文不會被覆蓋。"""
    run_id = _run(db, monkeypatch, "no_change")
    candidate = _candidate(db, run_id, "bye")
    with db._tx() as conn:
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
            "VALUES (?,?,?,?,?)",
            (candidate.entry_id, SRC_MANUAL, "已審核", "tester", "reviewed"),
        )
        db._refresh(conn, [candidate.entry_id])
    db.realign_smart_review_candidate(run_id, candidate.ordinal)

    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再會！")
        == "manual_reviewed"
    )
    assert db.entry_detail(candidate.entry_id).entry.zh_tw == "已審核"
    assert _candidate(db, run_id, "bye").status == "no_change"


def test_marks_show_latest_run_status_per_entry(db, monkeypatch):
    """條目列標記只看最近一次批次，ready 不標記，套用後即時反映為已套用。"""
    first = _run(db, monkeypatch, "no_change")
    bye = _candidate(db, first, "bye")
    hello = _candidate(db, first, "hello")
    marks = db.smart_review_marks([bye.entry_id, hello.entry_id])
    assert {m.status for m in marks.values()} == {"no_change"}
    assert {m.run_id for m in marks.values()} == {first}

    # 較新的批次覆蓋舊批次的標記；ready（待審查）不顯示。
    # hello 的建議少了 %s，所以在新批次是 invalid，bye 則是 ready。
    second = _run(db, monkeypatch, "suggest_revision")
    assert second != first
    marks = db.smart_review_marks([bye.entry_id, hello.entry_id])
    assert set(marks) == {hello.entry_id}
    assert marks[hello.entry_id].status == "invalid"

    ordinal = _candidate(db, second, "bye").ordinal
    assert db.apply_smart_review_candidate(second, ordinal) == "applied"
    marks = db.smart_review_marks([bye.entry_id, hello.entry_id])
    assert set(marks) == {bye.entry_id, hello.entry_id}
    assert marks[bye.entry_id].status == "applied"
    assert marks[bye.entry_id].run_id == second

    assert db.smart_review_marks([]) == {}


def test_marks_follow_manual_edit_status(db, monkeypatch):
    """人工編輯套用後，同一批次的標記變成已套用。"""
    run_id = _run(db, monkeypatch, "needs_context")
    candidate = _candidate(db, run_id, "bye")
    assert db.smart_review_marks([candidate.entry_id])[candidate.entry_id].status == (
        "needs_context"
    )
    db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再會！")
    assert db.smart_review_marks([candidate.entry_id])[candidate.entry_id].status == (
        "applied"
    )
