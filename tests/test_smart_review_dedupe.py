"""智慧校對依英文原文去重：選取只留代表條目，套用時一併寫入同狀態的同文條目。"""

from __future__ import annotations

import json

import pytest

from translation_tool.translation_db import KIND_LANG, TranslationDB, WriteBackItem
from translation_tool.translation_db.schema import SRC_MANUAL
from translation_tool.translation_db.smart_review import run_smart_review


@pytest.fixture
def db(tmp_path):
    """a_rep 與 b_twin 同文同譯；c_boots 同文但譯文不同；other 為另一模組的同文。"""
    database = TranslationDB(tmp_path / "dedupe.db")
    database.write_back(
        "1.20.1",
        [
            WriteBackItem(KIND_LANG, "aether", "a_rep", "Use: Shift", "使用：Shift"),
            WriteBackItem(KIND_LANG, "aether", "b_twin", "Use: Shift", "使用：Shift"),
            WriteBackItem(KIND_LANG, "aether", "c_boots", "Use: Shift", "按下 Shift"),
            WriteBackItem(KIND_LANG, "other", "a_rep", "Use: Shift", "使用：Shift"),
        ],
        fill_other_versions=False,
    )
    yield database
    database.close()


def _run(db, monkeypatch):
    """所有項目都建議同一份新譯文，並記下實際送出的條目數。"""
    sent: list[int] = []

    def fake_call(**kwargs):
        """模擬模型。"""
        items = kwargs["payload"]["items"]
        sent.append(len(items))
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "decision": "suggest_revision",
                        "suggested_zh_tw": "使用：Shift 鍵",
                        "issue_types": [],
                        "evidence_ids": [],
                        "confidence": 0.9,
                        "reason": "原文 Use: Shift 與目前譯文 使用：Shift 的語氣需要人工確認。",
                    }
                    for item in items
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    run = run_smart_review(db, model_name="gemini-test", api_key="k", limit=50)
    return run.run_id, sent


def _entry(db, mod_id: str, key: str):
    """依模組與 key 取得條目詳情。"""
    entry_id = db._one("SELECT id FROM entry WHERE mod_id=? AND key=?", (mod_id, key))[
        0
    ]
    return db.entry_detail(entry_id)


def test_selection_keeps_one_candidate_per_same_text_and_state(db, monkeypatch):
    """同模組、同英文、同目前狀態只送一筆；不同譯文或不同模組各自保留。"""
    run_id, sent = _run(db, monkeypatch)
    keys = sorted(
        (c.mod_id, c.key) for c in db.list_smart_review_candidates(run_id, limit=50)
    )
    assert keys == [("aether", "a_rep"), ("aether", "c_boots"), ("other", "a_rep")]
    assert sum(sent) == 3


def test_apply_writes_same_text_twins_with_own_history(db, monkeypatch):
    """套用代表條目時，被去重的同文條目一併寫入，各自留下 history，其餘不動。"""
    run_id, _ = _run(db, monkeypatch)
    rep = next(
        c
        for c in db.list_smart_review_candidates(run_id, limit=50)
        if (c.mod_id, c.key) == ("aether", "a_rep")
    )
    assert db.apply_smart_review_candidate(run_id, rep.ordinal) == "applied"

    for mod_id, key, expected, source in [
        ("aether", "a_rep", "使用：Shift 鍵", SRC_MANUAL),
        ("aether", "b_twin", "使用：Shift 鍵", SRC_MANUAL),
        ("aether", "c_boots", "按下 Shift", None),
        ("other", "a_rep", "使用：Shift", None),
    ]:
        entry = _entry(db, mod_id, key)
        assert entry.entry.zh_tw == expected, (mod_id, key)
        if source is not None:
            assert entry.entry.source == source
        history = [h for h in entry.history if h.action == "smart_review_apply"]
        assert len(history) == (1 if source is not None else 0), (mod_id, key)


def test_twin_with_reviewed_manual_row_is_not_overwritten(db, monkeypatch):
    """已有人工審核譯文的同文條目狀態不同，不會被去重也不會被覆蓋。"""
    chest = _entry(db, "aether", "b_twin")
    with db._tx() as conn:
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
            "VALUES (?,?,?,?,?)",
            (chest.entry.id, SRC_MANUAL, "人工確認", "tester", "reviewed"),
        )
        db._refresh(conn, [chest.entry.id])
    run_id, _ = _run(db, monkeypatch)
    rep = next(
        c
        for c in db.list_smart_review_candidates(run_id, limit=50)
        if (c.mod_id, c.key) == ("aether", "a_rep")
    )
    assert db.apply_smart_review_candidate(run_id, rep.ordinal) == "applied"
    assert _entry(db, "aether", "b_twin").entry.zh_tw == "人工確認"
