"""needs_context 升級：補相鄰鍵證據後重判，失敗時維持第一次結果。"""

from __future__ import annotations

import json

import pytest

from translation_tool.translation_db import KIND_LANG, TranslationDB, WriteBackItem
from translation_tool.translation_db.smart_review import run_smart_review
from translation_tool.translation_db.smart_review_escalation import (
    adjacent_key_context,
    merge_escalated_results,
)

CALL = "translation_tool.translation_db.smart_review_provider.call_gemini_requests"
LM_CONFIG = {"rate_limit": {"sleep_seconds_between_batches": 0}}


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "escalation.db")
    database.write_back(
        "1.21.1",
        [
            WriteBackItem(KIND_LANG, "demo", "config.speed", "Speed", "速度"),
            WriteBackItem(
                KIND_LANG,
                "demo",
                "config.speed.tooltip",
                "How fast it moves",
                "移動有多快",
            ),
            WriteBackItem(KIND_LANG, "demo", "other.thing", "Thing", "東西"),
        ],
        fill_other_versions=False,
    )
    # write_back 不會建立沒有譯文的條目；直接補一個未翻譯的相鄰鍵。
    with database._tx() as conn:
        conn.execute(
            "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
            "VALUES (?,?,?,?,?)",
            (KIND_LANG, "1.21.1", "demo", "config.speed.max", "Maximum"),
        )
    yield database
    database.close()


def _snapshot_for(db, key):
    from translation_tool.translation_db.models import SmartReviewSnapshot

    row = db._one(
        "SELECT id, kind, mc_version, mod_id, key, en_us FROM entry WHERE key=?", (key,)
    )
    return SmartReviewSnapshot(
        run_id="r",
        ordinal=1,
        entry_id=row[0],
        kind=row[1],
        mc_version=row[2],
        mod_id=row[3],
        key=row[4],
        en_us=row[5],
        current_zh_tw="x",
        effective_source=None,
        review_status=None,
        effective_revision=None,
    )


def test_adjacent_context_collects_parent_children_and_marks_untranslated(db):
    from translation_tool.translation_db.schema import SRC_MANUAL

    with db._tx() as conn:  # write_back 預設是 AI 來源；父鍵補一筆人工審核的譯文
        parent_id = conn.execute(
            "SELECT id FROM entry WHERE key='config.speed'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
            "VALUES (?,?,?,'','reviewed')",
            (parent_id, SRC_MANUAL, "速度"),
        )
    snapshot = _snapshot_for(db, "config.speed.tooltip")
    keys = {item["key"]: item for item in adjacent_key_context(db, snapshot)}

    assert set(keys) == {"config.speed", "config.speed.max"}
    assert keys["config.speed"]["zh_tw"] == "速度"
    assert keys["config.speed.max"]["review_status"] == "no_translation"
    assert all(item["reference_type"] == "adjacent_key" for item in keys.values())
    assert "other.thing" not in keys


def test_adjacent_context_includes_children_of_the_key(db):
    keys = {
        i["key"] for i in adjacent_key_context(db, _snapshot_for(db, "config.speed"))
    }
    assert {"config.speed.tooltip", "config.speed.max"} <= keys


def _responses(first_decision, second_decision):
    calls = []

    def call(**kwargs):
        calls.append(kwargs)
        decision = first_decision if len(calls) == 1 else second_decision
        items = []
        for item in kwargs["payload"]["items"]:
            adjacent = [
                c["id"]
                for c in item["context"]
                if c.get("reference_type") == "adjacent_key"
            ]
            this = decision if (len(calls) == 1 or adjacent) else "needs_context"
            items.append(
                {
                    "id": item["id"],
                    "decision": this,
                    "suggested_zh_tw": "",
                    "issue_types": [],
                    "evidence_ids": adjacent[:1] if len(calls) > 1 else [],
                    "confidence": 0.8,
                    "reason": "第一次缺資料" if len(calls) == 1 else "相鄰鍵顯示可保留",
                }
            )
        return json.dumps({"items": items})

    return call, calls


def _run(db, monkeypatch, call, **kwargs):
    monkeypatch.setattr(CALL, call)
    return run_smart_review(
        db,
        model_name="gemini-test",
        api_key="k",
        limit=1,
        batch_size=1,
        lm_config=LM_CONFIG,
        **kwargs,
    )


def test_needs_context_is_reasked_with_adjacent_keys_and_resolved(db, monkeypatch):
    call, calls = _responses("needs_context", "no_change")
    result = _run(db, monkeypatch, call)

    assert len(calls) == 2
    first_ctx = calls[0]["payload"]["items"][0]["context"]
    second_ctx = calls[1]["payload"]["items"][0]["context"]
    assert not any(c.get("reference_type") == "adjacent_key" for c in first_ctx)
    assert any(c.get("reference_type") == "adjacent_key" for c in second_ctx)
    assert result.metrics["context_escalation_attempts"] == 1
    assert result.metrics["context_escalation_resolved"] == 1
    assert result.needs_context_count == 0
    assert result.no_change_count == 1


def test_no_escalation_when_first_pass_is_decisive(db, monkeypatch):
    call, calls = _responses("no_change", "no_change")
    result = _run(db, monkeypatch, call)

    assert len(calls) == 1
    assert result.metrics["context_escalation_attempts"] == 0


def test_escalation_request_failure_keeps_first_result(db, monkeypatch):
    state = {"n": 0}
    first, _ = _responses("needs_context", "needs_context")

    def call(**kwargs):
        state["n"] += 1
        if state["n"] == 1:
            return first(**kwargs)
        raise RuntimeError("boom")

    result = _run(db, monkeypatch, call)

    assert result.needs_context_count == 1
    assert result.metrics["context_escalation_request_errors"] == 1


def test_adjacent_citations_move_into_reason_not_evidence_ids():
    from translation_tool.translation_db.models import SmartReviewSnapshot

    snapshot = SmartReviewSnapshot(
        "r",
        1,
        1,
        "lang",
        "1.21.1",
        "demo",
        "k",
        "K",
        "x",
        None,
        None,
        None,
        context=(
            {
                "id": "adj:9",
                "reference_type": "adjacent_key",
                "key": "k.tip",
                "zh_tw": "提示",
                "en_us": "Tip",
                "review_status": "reviewed",
            },
        ),
    )
    merged, resolved = merge_escalated_results(
        [{"id": "1", "decision": "needs_context", "reason": "缺", "evidence_ids": []}],
        [
            {
                "id": "1",
                "decision": "no_change",
                "reason": "可保留",
                "evidence_ids": ["adj:9"],
            }
        ],
        [snapshot],
    )

    assert resolved == 1
    assert merged[0]["evidence_ids"] == []
    assert "k.tip＝提示（reviewed）" in merged[0]["reason"]


# --- 驗證修復：多輪與暫時性故障就地重送 -----------------------------------------


def _item_response(item, *, valid):
    entry = {
        "id": item["id"],
        "decision": "no_change",
        "suggested_zh_tw": "",
        "issue_types": [],
        "evidence_ids": [],
        "confidence": 0.8,
    }
    if valid:
        entry["reason"] = "目前譯文可保留。"
    return entry


def _validation_call(plan):
    """plan[i] 為第 i 次呼叫的行為：True 回有效、False 回缺 reason、例外則拋出。"""
    calls = []

    def call(**kwargs):
        step = plan[min(len(calls), len(plan) - 1)]
        calls.append(kwargs)
        if isinstance(step, BaseException):
            raise step
        return json.dumps(
            {
                "items": [
                    _item_response(i, valid=step) for i in kwargs["payload"]["items"]
                ]
            }
        )

    return call, calls


def _http_error(status):
    import requests

    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(str(status), response=response)


def _no_sleep(monkeypatch):
    monkeypatch.setattr(
        "translation_tool.utils.cancellation.interruptible_sleep", lambda _s: None
    )


def test_validation_repair_transient_failure_is_resent(db, monkeypatch):
    _no_sleep(monkeypatch)
    call, calls = _validation_call([False, _http_error(503), True])
    result = _run(db, monkeypatch, call)

    assert len(calls) == 3  # 首次無效 + 修復遇到 503 + 原樣重送成功
    assert result.invalid_count == 0
    assert result.metrics["validation_repair_transient_retries"] == 1
    assert result.metrics["repaired_valid_count"] == 1


def test_validation_repair_runs_a_second_round_after_progress(db, monkeypatch):
    """第一輪修好一部分、第二輪處理剩下的；沒有進展就不再空轉。"""
    from translation_tool.translation_db import smart_review_repair as repair

    assert repair.SMART_REVIEW_VALIDATION_REPAIR_LIMIT == 2
    call, calls = _validation_call([False, False, True])
    result = _run(db, monkeypatch, call)

    # 第一輪修復仍無效、沒有進展 → 不進第二輪
    assert len(calls) == 2
    assert result.invalid_count == 1


def test_validation_repair_gives_up_after_bounded_transient_attempts(db, monkeypatch):
    _no_sleep(monkeypatch)
    call, calls = _validation_call([False, _http_error(503)])
    result = _run(db, monkeypatch, call)

    assert len(calls) == 1 + 3
    assert result.invalid_count == 1
    assert result.metrics["validation_repair_request_errors"] == 1


def test_validation_repair_second_round_resends_only_the_still_invalid(db, monkeypatch):
    calls = []

    def call(**kwargs):
        calls.append([i["id"] for i in kwargs["payload"]["items"]])
        items = kwargs["payload"]["items"]
        if len(calls) == 1:
            valid = [False] * len(items)
        elif len(calls) == 2:
            valid = [True] + [False] * (len(items) - 1)  # 第一輪只修好一筆
        else:
            valid = [True] * len(items)
        return json.dumps(
            {"items": [_item_response(i, valid=v) for i, v in zip(items, valid)]}
        )

    monkeypatch.setattr(CALL, call)
    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="k",
        limit=2,
        batch_size=2,
        lm_config=LM_CONFIG,
    )

    assert len(calls) == 3
    assert len(calls[2]) == 1  # 第二輪只重送仍無效的那一筆
    assert result.invalid_count == 0


def test_adjacent_context_does_not_cite_unreviewed_ai_translations(db):
    """AI 自己產生且未審核的譯文不能當佐證，否則模型會拿自己的輸出支持自己。"""
    from translation_tool.translation_db.schema import SRC_AI

    with db._tx() as conn:
        entry_id = conn.execute(
            "SELECT id FROM entry WHERE key='config.speed.max'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
            "VALUES (?,?,?,'','unreviewed')",
            (entry_id, SRC_AI, "AI 的最大值"),
        )
    snapshot = _snapshot_for(db, "config.speed.tooltip")
    item = next(
        i for i in adjacent_key_context(db, snapshot) if i["key"] == "config.speed.max"
    )

    assert item["zh_tw"] == ""
    assert item["review_status"] == "no_translation"


def test_untranslated_adjacent_citation_does_not_present_english_as_translation():
    from translation_tool.translation_db.models import SmartReviewSnapshot

    snapshot = SmartReviewSnapshot(
        "r",
        1,
        1,
        "lang",
        "1.21.1",
        "demo",
        "k",
        "K",
        "x",
        None,
        None,
        None,
        context=(
            {
                "id": "adj:9",
                "reference_type": "adjacent_key",
                "key": "k.max",
                "zh_tw": "",
                "en_us": "Maximum",
                "review_status": "no_translation",
            },
        ),
    )
    merged, _ = merge_escalated_results(
        [{"id": "1", "decision": "needs_context", "reason": "缺", "evidence_ids": []}],
        [
            {
                "id": "1",
                "decision": "no_change",
                "reason": "可保留",
                "evidence_ids": ["adj:9"],
            }
        ],
        [snapshot],
    )

    assert "k.max（尚無譯文；英文：Maximum）" in merged[0]["reason"]
    assert "k.max＝" not in merged[0]["reason"]


def test_adjacent_context_prefers_reviewed_and_labels_unreviewed_non_ai(db):
    """已審核優先；沒有時帶非 AI 的未審核譯文並標示 unreviewed。"""
    from translation_tool.translation_db.schema import SRC_MANUAL

    with db._tx() as conn:
        entry_id = conn.execute(
            "SELECT id FROM entry WHERE key='config.speed'"
        ).fetchone()[0]
        conn.execute("DELETE FROM translation WHERE entry_id=?", (entry_id,))
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
            "VALUES (?,?,?,'','unreviewed')",
            (entry_id, SRC_MANUAL, "人工未審核"),
        )
    snapshot = _snapshot_for(db, "config.speed.tooltip")

    def parent_item():
        return next(
            i for i in adjacent_key_context(db, snapshot) if i["key"] == "config.speed"
        )

    assert parent_item()["zh_tw"] == "人工未審核"
    assert parent_item()["review_status"] == "unreviewed"
    assert "NOT reviewed" in parent_item()["limitations"]

    with db._tx() as conn:
        conn.execute(
            "UPDATE translation SET review_status='reviewed' WHERE entry_id=?",
            (entry_id,),
        )
    assert parent_item()["review_status"] == "reviewed"
