"""智慧校對人工編輯覆蓋套用（含已套用重套）與快照對齊的測試。"""

from __future__ import annotations

import json

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    EntryFilter,
    TranslationDB,
    WriteBackItem,
)
from translation_tool.translation_db import smart_review as smart_review_module
from translation_tool.translation_db.schema import SRC_MANUAL
from translation_tool.translation_db.smart_review import run_smart_review
from translation_tool.translation_db.smart_review_history import SmartReviewHistoryStore


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


def _listed_keys(db, group: str | None) -> set[str]:
    """以智慧校對狀態篩選列出條目，回傳 key 集合。"""
    rows, total = db.list_entries(
        criteria=EntryFilter(version="1.21.1", smart_review=group), limit=50
    )
    assert total == len(rows)
    return {row.key for row in rows}


def test_entry_filter_by_smart_review_status_uses_latest_run(db, monkeypatch):
    """條目篩選依最近一次批次的狀態群組列出條目，且與數量一致。"""
    run_id = _run(db, monkeypatch, "no_change")
    assert _listed_keys(db, None) == {"hello", "bye"}
    assert _listed_keys(db, "no_change") == {"hello", "bye"}
    assert _listed_keys(db, "any") == {"hello", "bye"}
    assert _listed_keys(db, "applied") == set()
    assert _listed_keys(db, "invalid") == set()

    candidate = _candidate(db, run_id, "bye")
    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再會！")
        == "applied"
    )
    assert _listed_keys(db, "applied") == {"bye"}
    assert _listed_keys(db, "no_change") == {"hello"}


def test_entry_filter_groups_invalid_with_invalid_format(db, monkeypatch):
    """「無效／格式不符」群組同時涵蓋 invalid 與 invalid_format。"""
    _run(db, monkeypatch, "suggest_revision")  # hello 的建議缺 %s，所以 invalid
    assert _listed_keys(db, "invalid") == {"hello"}
    assert _listed_keys(db, "stale") == set()


def test_entry_filter_rejects_unknown_smart_review_value():
    """未知的智慧校對篩選值在建立條件時就被拒絕。"""
    with pytest.raises(ValueError, match="未知智慧校對狀態"):
        EntryFilter(version="1.21.1", smart_review="nope")


def test_manual_edit_reports_changed_when_source_text_changes_first(db, monkeypatch):
    """編輯期間英文原文被改動時回報 changed，而不是用舊原文判成 invalid_format。"""
    run_id = _run(db, monkeypatch, "no_change")
    candidate = _candidate(db, run_id, "bye")
    db.realign_smart_review_candidate(run_id, candidate.ordinal)
    with db._tx() as conn:
        conn.execute(
            "UPDATE entry SET en_us=? WHERE id=?", ("Goodbye %s", candidate.entry_id)
        )

    # 新原文要求 %s，草稿沒有；但資料已變動，應先回報 changed。
    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再會！")
        == "changed"
    )
    assert _candidate(db, run_id, "bye").status == "no_change"


def test_manual_edit_retries_transient_state_write_failure(db, monkeypatch):
    """短暫的檔案鎖（回寫兩次失敗後成功）不影響結果，仍回報 applied。"""
    run_id = _run(db, monkeypatch, "no_change")
    candidate = _candidate(db, run_id, "bye")
    db.realign_smart_review_candidate(run_id, candidate.ordinal)
    monkeypatch.setattr(smart_review_module.time, "sleep", lambda _s: None)
    real = SmartReviewHistoryStore.update_candidate
    calls = []

    def flaky(self, *args, **kwargs):
        """前兩次模擬 Windows 檔案鎖。"""
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError("locked")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(SmartReviewHistoryStore, "update_candidate", flaky)
    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再會！")
        == "applied"
    )
    assert len(calls) == 3
    assert _candidate(db, run_id, "bye").status == "applied"


def test_manual_edit_reports_state_sync_failure_and_reconciles(db, monkeypatch):
    """SQLite 已提交但 JSON 一直寫不進去：明確回報，下次讀取依 history 補回，不重複套用。"""
    run_id = _run(db, monkeypatch, "no_change")
    candidate = _candidate(db, run_id, "bye")
    db.realign_smart_review_candidate(run_id, candidate.ordinal)
    monkeypatch.setattr(smart_review_module.time, "sleep", lambda _s: None)
    real = SmartReviewHistoryStore.update_candidate

    def broken(self, *args, **kwargs):
        """模擬 JSON 狀態回寫一直失敗。"""
        raise PermissionError("locked")

    monkeypatch.setattr(SmartReviewHistoryStore, "update_candidate", broken)
    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再會！")
        == "state_sync_failed"
    )
    monkeypatch.setattr(SmartReviewHistoryStore, "update_candidate", real)

    # 譯文與 history 已提交，不回滾；JSON 仍是舊狀態。
    entry = db.entry_detail(candidate.entry_id).entry
    assert (entry.source, entry.zh_tw) == (SRC_MANUAL, "再會！")
    assert len(_apply_history(db, candidate.entry_id)) == 1
    assert _candidate_raw_status(db, run_id, candidate.ordinal) == "no_change"

    # 下一次讀取批次時對帳補回 applied，且指向 SQLite 的套用批次。
    assert _candidate(db, run_id, "bye").status == "applied"
    batch = _apply_history(db, candidate.entry_id)[0].batch
    assert _candidate(db, run_id, "bye").applied_batch == batch
    # 載入草稿時會先對齊快照；同樣內容再套用視為沒有變更，不重複寫入。
    db.realign_smart_review_candidate(run_id, candidate.ordinal)
    assert (
        db.apply_smart_review_manual_edit(run_id, candidate.ordinal, "再會！")
        == "unchanged"
    )
    assert len(_apply_history(db, candidate.entry_id)) == 1


def _candidate_raw_status(db, run_id: str, ordinal: int) -> str:
    """直接讀批次 JSON 的候選狀態（不經過對帳）。"""
    return SmartReviewHistoryStore(db.path).get_candidate(run_id, ordinal)["status"]


def _write_run(store: SmartReviewHistoryStore, run_id: str, started_at: str, body):
    """寫一份手工批次 JSON；``body`` 為 candidates 欄位內容。"""
    store.directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "id": run_id,
        "started_at": started_at,
        "candidates": body,
    }
    store.path_for(run_id).write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("bad", [123, "abc", {"a": 1}, None, True])
def test_latest_marks_skips_corrupt_candidates_structure(tmp_path, bad):
    """candidates 不是 list 的批次被略過並記錄警告，改用次新的有效批次。"""
    store = SmartReviewHistoryStore(tmp_path / "x.db")
    _write_run(
        store, "older", "2026-10-01 00:00:00", [{"entry_id": 7, "status": "applied"}]
    )
    _write_run(store, "newer", "2026-10-02 00:00:00", bad)
    assert store.latest_run_marks() == ("older", {7: ("applied", None)})
    # 損壞批次已記住，不會每次換頁重複解析
    assert store.latest_run_marks() == ("older", {7: ("applied", None)})

    store.path_for("older").unlink()
    assert store.latest_run_marks() == (None, {})


def test_latest_marks_tolerates_bad_candidate_items(tmp_path):
    """候選項目缺欄位或型別不對時略過該筆，其餘照常。"""
    store = SmartReviewHistoryStore(tmp_path / "x.db")
    _write_run(
        store,
        "run",
        "2026-10-02 00:00:00",
        [
            "oops",
            {"status": "applied"},
            {"entry_id": "x", "status": "applied"},
            {"entry_id": 3, "status": "invalid", "validation_error": "e"},
        ],
    )
    assert store.latest_run_marks() == ("run", {3: ("invalid", "e")})


def test_latest_marks_parses_only_latest_run_and_keeps_one_cache(tmp_path, monkeypatch):
    """大量歷史批次：只完整解析最近一份，快取不保留歷史批次的候選狀態。"""
    store = SmartReviewHistoryStore(tmp_path / "x.db")
    candidates = [{"entry_id": i, "status": "applied"} for i in range(300)]
    for index in range(200):
        _write_run(
            store,
            f"old{index:03d}",
            f"2026-01-{index % 28 + 1:02d} 00:00:00",
            candidates,
        )
    _write_run(
        store, "latest", "2026-12-31 23:59:59", [{"entry_id": 1, "status": "stale"}]
    )

    parsed = []
    real = SmartReviewHistoryStore._parse_marks

    def counting(path):
        """記錄被完整解析的檔案。"""
        parsed.append(path.stem)
        return real(path)

    monkeypatch.setattr(SmartReviewHistoryStore, "_parse_marks", staticmethod(counting))
    assert store.latest_run_marks() == ("latest", {1: ("stale", None)})
    assert parsed == ["latest"]
    # 再次呼叫（換頁）完全命中快取，不再解析
    assert store.latest_run_marks() == ("latest", {1: ("stale", None)})
    assert parsed == ["latest"]
    assert list(SmartReviewHistoryStore._latest_marks).count(store.directory) == 1
    cached = SmartReviewHistoryStore._latest_marks[store.directory]
    assert len(cached[3]) == 1  # 只保留最近一次批次的 1 筆，而不是 200 批 × 300 筆
