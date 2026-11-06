"""智慧校對批次大小：語系分組尾端的短批次不能讓後續所有批次都縮成同樣大小。

候選依來源語系分組，每個請求只含單一語系，所以每一組最後一批可能比設定的批次大小短。
自適應批次大小只該因為真正的失敗（超過輸入預算、回應被截斷或結構錯誤）而縮小。
"""

from __future__ import annotations

import json

import pytest

from translation_tool.translation_db import KIND_KUBEJS_LANG, ScanItem, TranslationDB
from translation_tool.translation_db.smart_review_run import run_smart_review

V = "1.21.1"
CALL = "translation_tool.translation_db.smart_review_provider.call_gemini_requests"
LM_CONFIG = {"rate_limit": {"sleep_seconds_between_batches": 0}}


def _scan(index: int, locale: str) -> ScanItem:
    key = f"tip.{locale}.{index:03d}"
    return ScanItem(
        KIND_KUBEJS_LANG,
        "demo@pack",
        key,
        f"Text {locale} {index}",
        f"譯文 {locale} {index}",
        source_path=f"kubejs/lang/{locale}.json",
        source_selector=f"lang:{locale}:{key}",
        source_hash=f"h-{key}",
        source_format="lang-json",
        pack_scope="pack",
        source_locale=locale,
    )


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "batches.db")
    # 預設語系（en_us）只有 2 筆；另一個語系有 11 筆
    database.ingest(
        V,
        [_scan(i, "en_us") for i in range(2)] + [_scan(i, "pt_br") for i in range(11)],
    )
    yield database
    database.close()


def _run(db, monkeypatch, *, fail_first_pt_batch=False):
    sizes: list[int] = []

    def call(**kwargs):
        items = kwargs["payload"]["items"]
        sizes.append(len(items))
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "decision": "no_change",
                        "suggested_zh_tw": "",
                        "issue_types": [],
                        "evidence_ids": [],
                        "confidence": 0.9,
                        "reason": "目前譯文可保留。",
                    }
                    for item in items
                ]
            }
        )

    monkeypatch.setattr(CALL, call)
    events: list[dict] = []
    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="k",
        limit=100,
        version=V,
        effective_source=None,
        batch_size=5,
        lm_config=LM_CONFIG,
        progress=events.append,
    )
    return result, sizes, events


def test_short_locale_group_does_not_shrink_the_following_batches(db, monkeypatch):
    result, sizes, _events = _run(db, monkeypatch)

    # en_us 組 2 筆；pt_br 組 11 筆 = 5 + 5 + 1
    assert sizes == [2, 5, 5, 1]
    assert result.metrics["shrink_count"] == 0


def test_planned_batch_count_follows_the_locale_groups(db, monkeypatch):
    """預估批數依語系分組計算（2 筆 + 11 筆各自進位），不是 13 筆除以批次大小。"""
    _result, sizes, events = _run(db, monkeypatch)

    totals = {
        snapshot["event"]["batch_total"]
        for snapshot in events
        if snapshot.get("event", {}).get("kind") == "batch_start"
    }
    assert totals == {len(sizes)} == {4}


def test_resume_with_locale_short_batches_and_a_413_shrink(db, monkeypatch):
    """續跑＋多語系短批次＋失敗縮批：三者同時發生時，所有候選仍恰好審查一次。

    第一次執行在 en_us 組（2 筆）完成後取消；續跑時 pt_br 組（11 筆）的第一個請求回傳
    HTTP 413。驗證縮批只計一次（來自真正的失敗），而且 13 筆候選全部都被審查、
    pt_br 組每筆只成功送出一次。
    """
    import requests

    from translation_tool.translation_db.smart_review_run import resume_smart_review

    seen: list[list[str]] = []
    state = {"phase": "first", "fail_next": False}

    def call(**kwargs):
        items = kwargs["payload"]["items"]
        if state["phase"] == "resume" and state["fail_next"]:
            state["fail_next"] = False
            response = requests.Response()
            response.status_code = 413
            response._content = json.dumps(
                {"error": {"message": "request payload too large"}}
            ).encode()
            raise requests.HTTPError(response=response)
        seen.append([item["id"] for item in items])
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "decision": "no_change",
                        "suggested_zh_tw": "",
                        "issue_types": [],
                        "evidence_ids": [],
                        "confidence": 0.9,
                        "reason": "目前譯文可保留。",
                    }
                    for item in items
                ]
            }
        )

    monkeypatch.setattr(CALL, call)
    first = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="k",
        limit=100,
        version=V,
        effective_source=None,
        batch_size=5,
        lm_config=LM_CONFIG,
        cancel_check=lambda: bool(seen),  # 第一個請求（en_us 組）完成後取消
    )
    assert [len(batch) for batch in seen] == [2]
    assert first.status == "cancelled"

    state.update(phase="resume", fail_next=True)
    resumed = resume_smart_review(
        db,
        run_id=first.run_id,
        api_key="k",
        model_name="gemini-test",
        batch_size=5,
        lm_config=LM_CONFIG,
    )

    assert resumed.status == "completed"
    assert resumed.processed_count == 13
    assert resumed.metrics["shrink_count"] == 1  # 只有那次真正的 413 縮批
    resume_ids = [item for batch in seen[1:] for item in batch]
    # 已完成的 en_us 組不會重送：續跑只送 pt_br 組的 11 筆，每筆恰好一次
    assert len(resume_ids) == len(set(resume_ids)) == 11
    assert set(resume_ids).isdisjoint(seen[0])
    assert resumed.metrics["invalidated_by_manual_edit_count"] == 0
    assert max(len(batch) for batch in seen[1:]) < 5  # 失敗後確實縮小


def test_resume_of_a_completed_run_does_not_resend_reviewed_candidates(db, monkeypatch):
    """已完成的候選帶有本地證據（context）時，續跑不能把它們當成「已變動」而重審。"""
    from translation_tool.translation_db.smart_review_run import resume_smart_review

    result, sizes, _events = _run(db, monkeypatch)
    assert sizes == [2, 5, 5, 1]

    resent: list[int] = []

    def call(**kwargs):
        resent.append(len(kwargs["payload"]["items"]))
        raise AssertionError("已完成的候選不該被重新送審")

    monkeypatch.setattr(CALL, call)
    resumed = resume_smart_review(
        db,
        run_id=result.run_id,
        api_key="k",
        model_name="gemini-test",
        batch_size=5,
        lm_config=LM_CONFIG,
    )

    assert resent == []
    assert resumed.metrics["invalidated_by_manual_edit_count"] == 0
