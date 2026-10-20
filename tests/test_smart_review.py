from __future__ import annotations

import json
import sqlite3

import pytest
import requests

from translation_tool.translation_db import (
    KIND_ADVANCEMENT,
    KIND_GUIDE,
    KIND_LANG,
    KIND_PATCHOULI,
    TranslationDB,
    WriteBackItem,
)
from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.schema import SRC_AI, SRC_MANUAL
from translation_tool.translation_db.smart_review import (
    build_smart_review_payload,
    parse_smart_review_response,
    run_smart_review,
    smart_review_response_schema,
)


def _seed(db: TranslationDB) -> None:
    """建立智慧校對測試資料。"""
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(KIND_LANG, "demo", "hello", "Hello %s", "你好 %s"),
            WriteBackItem(KIND_LANG, "demo", "bye", "Goodbye", "再見"),
        ],
        fill_other_versions=False,
    )


@pytest.fixture
def db(tmp_path):
    """建立測試用資料庫並在測試結束後清理。"""
    database = TranslationDB(tmp_path / "smart-review.db")
    _seed(database)
    yield database
    database.close()


def test_snapshot_uses_implicit_unreviewed_for_non_manual_sources(db):
    """驗證非人工來源使用隱含未審核狀態建立候選快照。"""
    snapshots = db.smart_review_candidates(version="1.21.1", limit=1000)
    assert [item.key for item in snapshots] == ["bye", "hello"]
    assert all(item.review_status == "unreviewed" for item in snapshots)
    assert all(item.effective_source != SRC_MANUAL for item in snapshots)


def test_smart_review_payload_carries_target_version(db):
    """驗證模型負載包含目標 Minecraft 版本以避免跨版本誤用 context。"""
    snapshot = db.smart_review_candidates(version="1.21.1", limit=1)[0]
    assert build_smart_review_payload([snapshot])["items"][0]["mc_version"] == "1.21.1"


@pytest.mark.parametrize(
    "filters",
    [
        {"effective_source": SRC_AI},
        {"kind": KIND_LANG, "query": "hello"},
        {"review_status": "unreviewed", "mod_id": "demo"},
    ],
)
def test_candidate_count_uses_the_same_scope_as_rows(db, filters):
    """驗證候選數量與資料列使用相同範圍。"""
    snapshots = db.smart_review_candidates(version="1.21.1", limit=1000, **filters)

    assert db.count_smart_review_candidates(version="1.21.1", **filters) == len(
        snapshots
    )


def test_snapshot_can_require_unique_reviewed_same_key_reference(db):
    """驗證候選快照可以要求唯一同鍵已審核參考。"""
    db.write_back(
        "1.20.1",
        [WriteBackItem(KIND_LANG, "demo", "hello", "Hello %s", "嗨 %s")],
        fill_other_versions=False,
    )
    reviewed = next(
        item for item in db.list_entries("1.21.1")[0] if item.key == "hello"
    )
    db.review_manual(reviewed.id, expected_zh_tw="你好 %s", propagate=False)

    snapshots = db.smart_review_candidates(
        version="1.20.1", limit=1000, require_reviewed_reference=True
    )
    assert [item.key for item in snapshots] == ["hello"]
    assert db.count_smart_review_candidates(
        version="1.20.1", require_reviewed_reference=True
    ) == len(snapshots)


def test_reviewed_reference_requires_same_english_source(db):
    """驗證已審核參考必須使用相同英文原文。"""
    db.write_back(
        "1.20.1",
        [WriteBackItem(KIND_LANG, "demo", "hello", "Hello changed", "嗨")],
        fill_other_versions=False,
    )
    changed = next(item for item in db.list_entries("1.20.1")[0] if item.key == "hello")
    db.review_manual(changed.id, expected_zh_tw="嗨", propagate=False)

    assert (
        db.smart_review_candidates(
            version="1.21.1", limit=1000, require_reviewed_reference=True
        )
        == []
    )


def test_local_context_covers_non_lang_terms_and_preserves_traceability(db):
    """驗證本地上下文涵蓋非 lang 專有名詞並保留可追溯性。"""
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(
                KIND_LANG,
                "demo",
                "phenolic_resin",
                "Phenolic Resin",
                "酚醛樹脂",
            ),
            WriteBackItem(
                KIND_PATCHOULI,
                "demo",
                "book/page",
                "Phenolic Resin is useful",
                "酚醛樹脂很有用",
            ),
            WriteBackItem(
                KIND_GUIDE,
                "demo",
                "guide/page",
                "Phenolic Resin guide",
                "酚醛樹脂指南",
            ),
            WriteBackItem(
                KIND_ADVANCEMENT,
                "demo",
                "advancement/test",
                "Phenolic Resin advancement",
                "酚醛樹脂進度",
            ),
        ],
        fill_other_versions=False,
    )
    term = next(
        item for item in db.list_entries("1.21.1")[0] if item.key == "phenolic_resin"
    )
    db.review_manual(term.id, expected_zh_tw="酚醛樹脂", propagate=False)
    candidate = db.smart_review_candidates(
        version="1.21.1",
        mod_id="demo",
        kind=KIND_PATCHOULI,
        effective_source=SRC_AI,
        limit=1000,
    )[0]
    glossary = [
        item
        for item in candidate.context
        if item["reference_type"] == "reviewed_lang_glossary"
    ]
    assert glossary
    assert glossary[0]["id"].startswith("db:")
    assert glossary[0]["en_us"] == "Phenolic Resin"
    assert glossary[0]["zh_tw"] == "酚醛樹脂"
    assert glossary[0]["mod_id"] == "demo"
    assert glossary[0]["review_status"] == "reviewed"


def test_local_context_quality_and_evidence_ids_keep_source_identity(db):
    """驗證格式不合格參考不會升為高信心且不同來源 id 不碰撞。"""
    hello = next(item for item in db.list_entries("1.21.1")[0] if item.key == "hello")
    with db._tx() as conn:
        conn.execute(
            "UPDATE translation SET zh_tw=?, review_status='reviewed' "
            "WHERE entry_id=? AND source=?",
            ("你好", hello.id, SRC_AI),
        )
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw, review_status) "
            "VALUES (?,?,?,'reviewed')",
            (hello.id, SRC_MANUAL, "您好 %s"),
        )

    context = db._smart_review_context(
        0, KIND_LANG, "1.21.1", "demo", "hello", "Hello %s"
    )
    database_rows = [item for item in context if item.get("entry_id") == hello.id]
    assert len({item["id"] for item in database_rows}) == len(database_rows)
    assert any(
        item["quality_status"] == "mismatch"
        and item["confidence"] == "low"
        and "格式問題" in item["limitations"]
        for item in database_rows
    )


def test_smart_review_query_escapes_like_wildcards(db):
    """驗證智慧校對搜尋中的百分比、底線與反斜線是字面值。"""
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(KIND_LANG, "demo", "foo_bar", "Foo Bar", " foo "),
            WriteBackItem(KIND_LANG, "demo", "foo%bar", "Foo Percent", " percent "),
            WriteBackItem(KIND_LANG, "demo", r"foo\\bar", "Foo Slash", " slash "),
        ],
        fill_other_versions=False,
    )
    assert db.count_smart_review_candidates(version="1.21.1", query="foo_bar") == 1
    assert db.count_smart_review_candidates(version="1.21.1", query="foo%bar") == 1
    assert db.count_smart_review_candidates(version="1.21.1", query=r"foo\\bar") == 1
    assert db.count_smart_review_candidates(version="1.21.1", query="foo_bar") != 3


def test_local_context_uses_shared_terminology_filter(db, monkeypatch):
    """驗證本地上下文使用共用的專有名詞篩選器。"""
    import translation_tool.translation_db.smart_review as smart_review_module
    from translation_tool.core.official_locale import OfficialLocaleIndex

    monkeypatch.setattr(
        smart_review_module,
        "load_official_locale_index",
        lambda: OfficialLocaleIndex({}, {}, {}, "test"),
    )
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(
                KIND_LANG,
                "demo",
                "fire_resistance",
                "Fire Resistance",
                "抗火",
            ),
            WriteBackItem(
                KIND_LANG,
                "demo",
                "close",
                "Close",
                "關閉",
            ),
            WriteBackItem(
                KIND_PATCHOULI,
                "demo",
                "fire_context",
                "§9Fire Resistance (1:00)",
                "§9抗火 (1:00)",
            ),
            WriteBackItem(
                KIND_PATCHOULI,
                "demo",
                "close_context",
                "Calms attacking Piglins when Player gets close",
                "當玩家靠近時能安撫攻擊中的豬布林",
            ),
        ],
        fill_other_versions=False,
    )
    entries = db.list_entries("1.21.1")[0]
    db.review_manual(
        next(item for item in entries if item.key == "fire_resistance").id,
        expected_zh_tw="抗火",
        propagate=False,
    )
    db.review_manual(
        next(item for item in entries if item.key == "close").id,
        expected_zh_tw="關閉",
        propagate=False,
    )

    fire = next(
        item
        for item in db.smart_review_candidates(
            version="1.21.1",
            mod_id="demo",
            kind=KIND_PATCHOULI,
            effective_source=SRC_AI,
            limit=1000,
        )
        if item.key == "fire_context"
    )
    fire_terms = [
        item
        for item in fire.context
        if item["reference_type"] == "reviewed_lang_glossary"
    ]
    assert [(item["en_us"], item["zh_tw"]) for item in fire_terms] == [
        ("Fire Resistance", "抗火")
    ]

    close = next(
        item
        for item in db.smart_review_candidates(
            version="1.21.1",
            mod_id="demo",
            kind=KIND_PATCHOULI,
            effective_source=SRC_AI,
            limit=1000,
        )
        if item.key == "close_context"
    )
    assert not [
        item
        for item in close.context
        if item["reference_type"] in {"reviewed_lang_glossary", "official_locale"}
    ]


def test_response_validation_requires_exact_ids_and_format_safe_suggestion(db):
    """驗證回應驗證要求精確識別碼與格式安全建議。"""
    snapshot = db.smart_review_candidates(version="1.21.1", limit=2)[1]
    valid = {
        "items": [
            {
                "id": str(snapshot.ordinal),
                "decision": "suggest_revision",
                "suggested_zh_tw": "你好啊 %s",
                "issue_types": ["style"],
                "evidence_ids": [],
                "confidence": 0.9,
                "reason": "原文 Hello %s 與目前譯文 你好 %s 的語氣需要調整。",
            }
        ]
    }
    assert parse_smart_review_response(json.dumps(valid), [snapshot])[0][
        "decision"
    ] == ("suggest_revision")

    invalid = dict(valid)
    invalid["items"] = [dict(valid["items"][0], suggested_zh_tw="你好啊")]
    result = parse_smart_review_response(json.dumps(invalid), [snapshot])
    assert result[0]["validation_error"] == "suggested_zh_tw 未通過格式與結構驗證"

    with pytest.raises(ValueError, match="id 集合"):
        parse_smart_review_response(
            json.dumps({"items": [dict(valid["items"][0], id="wrong")]}),
            [snapshot],
        )


def test_response_validation_requires_concrete_reason_and_rejects_external_urls(db):
    """驗證回應驗證要求具體理由並拒絕外部網址。"""
    snapshot = db.smart_review_candidates(version="1.21.1", limit=2)[1]
    base = {
        "id": str(snapshot.ordinal),
        "decision": "suggest_revision",
        "suggested_zh_tw": "你好啊 %s",
        "issue_types": ["terminology"],
        "evidence_ids": [],
        "confidence": 0.9,
    }
    missing = {"items": [base]}
    result = parse_smart_review_response(json.dumps(missing), [snapshot])
    assert "欄位" in result[0]["validation_error"]

    vague = {"items": [dict(base, reason="建議優化。")]}  # 沒有可核對內容
    result = parse_smart_review_response(json.dumps(vague), [snapshot])
    assert "reason" in result[0]["validation_error"]

    external = {
        "items": [
            dict(
                base,
                reason="原文 Hello %s 與目前譯文 你好 %s 不一致，請見 https://example.invalid。",
            )
        ]
    }
    result = parse_smart_review_response(json.dumps(external), [snapshot])
    assert "URL" in result[0]["validation_error"]


def test_run_persists_candidates_without_writing_translation(db, monkeypatch):
    """驗證執行流程只保存校對候選而不寫入翻譯。"""

    def fake_call(**kwargs):
        """模擬模型請求並回傳測試回應。"""
        items = kwargs["payload"]["items"]
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "decision": "no_change",
                        "suggested_zh_tw": "",
                        "issue_types": [],
                        "evidence_ids": [],
                        "confidence": 0.8,
                        "reason": "英文與目前譯文一致，暫不修改。",
                    }
                    for item in items
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    before = db._q("SELECT COUNT(*) FROM translation WHERE source=?", (SRC_MANUAL,))[0][
        0
    ]
    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=1,
    )
    assert result.selected_count == result.processed_count == 2
    assert result.valid_count == 2
    assert result.no_change_count == 2
    assert (
        db._q("SELECT COUNT(*) FROM translation WHERE source=?", (SRC_MANUAL,))[0][0]
        == before
    )
    stored = db.list_smart_review_candidates(result.run_id)
    assert [item.status for item in stored] == ["no_change", "no_change"]
    run = db.get_smart_review_run(result.run_id)
    assert run["scope"]["effective_source"] == SRC_AI
    assert db.list_smart_review_runs(limit=1)[0]["id"] == result.run_id


def test_request_failures_and_cancellation_are_distinct_from_invalid(db, monkeypatch):
    """驗證請求失敗、取消與無效回應會被正確區分。"""

    def fail_call(**_kwargs):
        """模擬模型請求失敗。"""
        raise RuntimeError("simulated request failure")

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fail_call,
    )
    failed = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=1,
    )
    assert failed.status == "partial"
    assert failed.request_error_count == 2
    assert [item.status for item in db.list_smart_review_candidates(failed.run_id)] == [
        "request_failed",
        "request_failed",
    ]

    called = False

    def should_not_call(**_kwargs):
        """在不應送出模型請求時讓測試失敗。"""
        nonlocal called
        called = True
        raise AssertionError("cancelled run must not send a request")

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        should_not_call,
    )
    cancelled = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=1,
        cancel_check=lambda: True,
    )
    assert not called
    assert cancelled.status == "cancelled"
    assert [
        item.status for item in db.list_smart_review_candidates(cancelled.run_id)
    ] == ["cancelled", "cancelled"]


def test_smart_review_schema_keeps_provider_unsupported_limits_local():
    """驗證智慧校對結構讓提供者不支援的限制留在本地驗證。"""
    reason_schema = smart_review_response_schema(["1"])["properties"]["items"]["items"][
        "properties"
    ]["reason"]

    assert reason_schema == {"type": "string"}


def test_smart_review_forwards_chatgpt_runtime_options(db, monkeypatch):
    """驗證智慧校對會轉發 ChatGPT 執行選項。"""
    captured = {}

    def fake_call(**kwargs):
        """模擬模型請求並回傳測試回應。"""
        captured.update(kwargs)
        item = kwargs["payload"]["items"][0]
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "decision": "no_change",
                        "suggested_zh_tw": "",
                        "issue_types": [],
                        "evidence_ids": [],
                        "confidence": 0.8,
                        "reason": "目前譯文可保留。",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.chatgpt_account_status",
        lambda: {"active_profile_id": "profile-a"},
    )

    result = run_smart_review(
        db,
        model_name="gpt-5.6-luna",
        provider="chatgpt",
        api_key="",
        lm_config={
            "fast": True,
            "chatgpt_model_profile_id": "profile-a",
            "chatgpt_model_settings": {"gpt-5.6-luna": {"reasoning_effort": "high"}},
        },
        limit=1,
    )

    assert result.valid_count == 1
    assert captured["fast"] is True
    assert captured["reasoning_effort"] == "high"
    assert captured["profile_id"] == "profile-a"


def test_smart_review_rotates_gemini_key_for_retryable_http_error(db, monkeypatch):
    """驗證智慧校對遇到可重試 HTTP 錯誤時會輪替 Gemini 金鑰。"""
    from translation_tool.core import lm_config_rules

    calls = []

    def fake_call(**kwargs):
        """模擬模型請求並回傳測試回應。"""
        calls.append(kwargs["api_key"])
        if len(calls) == 1:
            response = requests.Response()
            response.status_code = 429
            raise requests.HTTPError(response=response)
        item = kwargs["payload"]["items"][0]
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "decision": "no_change",
                        "suggested_zh_tw": "",
                        "issue_types": [],
                        "evidence_ids": [],
                        "confidence": 0.8,
                        "reason": "目前譯文可保留。",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    monkeypatch.setattr(lm_config_rules, "_get_all_keys", lambda: ["key0", "key1"])
    lm_config_rules._key_tracker.reset()

    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="",
        limit=1,
    )

    assert result.valid_count == 1
    assert result.request_error_count == 0
    assert calls == ["key0", "key1"]
    lm_config_rules._key_tracker.reset()


def test_apply_candidate_is_cas_guarded_and_writes_history(db, monkeypatch):
    """驗證套用候選受 CAS 保護並寫入歷史。"""

    def fake_call(**kwargs):
        """模擬模型請求並回傳測試回應。"""
        return json.dumps(
            {
                "items": [
                    {
                        "id": kwargs["payload"]["items"][0]["id"],
                        "decision": "suggest_revision",
                        "suggested_zh_tw": "再見！",
                        "issue_types": ["style"],
                        "evidence_ids": [],
                        "confidence": 0.95,
                        "reason": "原文 Goodbye 與目前譯文 再見 仍可更精確地表達語氣。",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    result = run_smart_review(db, model_name="gemini-test", api_key="test-key", limit=1)
    assert db.apply_smart_review_candidate(result.run_id, 1) == "applied"
    candidate = db.list_smart_review_candidates(result.run_id)[0]
    assert candidate.status == "applied"
    assert db.entry_detail(candidate.entry_id).entry.source == SRC_MANUAL
    assert any(
        row.action == "smart_review_apply"
        for row in db.entry_detail(candidate.entry_id).history
    )

    # A second apply is no longer eligible, and a changed effective row cannot be overwritten.
    assert db.apply_smart_review_candidate(result.run_id, 1) == "not_ready"
    history = next(
        row
        for row in db.entry_detail(candidate.entry_id).history
        if row.action == "smart_review_apply"
    )
    assert db.revert(history.id) == 1
    assert db.entry_detail(candidate.entry_id).entry.source != SRC_MANUAL


def test_draft_is_persisted_and_apply_uses_the_human_value(db, monkeypatch):
    """驗證草稿會保存且套用時使用人工值。"""

    def fake_call(**kwargs):
        """模擬模型請求並回傳測試回應。"""
        return json.dumps(
            {
                "items": [
                    {
                        "id": kwargs["payload"]["items"][0]["id"],
                        "decision": "suggest_revision",
                        "suggested_zh_tw": "再見！",
                        "issue_types": ["terminology"],
                        "evidence_ids": [],
                        "confidence": 0.95,
                        "reason": "原文 Goodbye 與目前譯文 再見 的專有名詞語氣需要確認。",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    result = run_smart_review(db, model_name="gemini-test", api_key="test-key", limit=1)
    assert db.save_smart_review_draft(result.run_id, 1, "再會！") == "saved"
    stored = db.list_smart_review_candidates(result.run_id)[0]
    assert stored.draft_zh_tw == "再會！"
    assert db.apply_smart_review_candidate(result.run_id, 1) == "applied"
    assert db.entry_detail(stored.entry_id).entry.zh_tw == "再會！"


def test_draft_preserves_spaces_and_newlines_until_apply(db, monkeypatch):
    """驗證人工草稿的前後空白與實際換行不會被靜默改寫。"""
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(
                KIND_LANG,
                "demo",
                "multiline",
                "Line one\nLine two",
                "第一行\n第二行",
            )
        ],
        fill_other_versions=False,
    )

    def fake_call(**kwargs):
        """模擬可供草稿編輯的校對回應。"""
        item = kwargs["payload"]["items"][0]
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "decision": "suggest_revision",
                        "suggested_zh_tw": "第一列\n第二列",
                        "issue_types": ["style"],
                        "evidence_ids": [],
                        "confidence": 0.9,
                        "reason": "原文 Line one 與目前譯文 第一行需要人工確認。",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=1,
        query="multiline",
    )
    draft = "  第一行\n第二行  "
    assert db.save_smart_review_draft(result.run_id, 1, draft) == "saved"
    assert db.list_smart_review_candidates(result.run_id)[0].draft_zh_tw == draft
    assert db.apply_smart_review_candidate(result.run_id, 1) == "applied"
    entry_id = db.list_smart_review_candidates(result.run_id)[0].entry_id
    assert db.entry_detail(entry_id).entry.zh_tw == draft


def test_smart_review_shrinks_truncated_batches_and_keeps_all_ordinals(db, monkeypatch):
    """驗證截斷回應會有界縮批且成功子批次仍逐筆保存。"""
    calls = []

    def fake_call(**kwargs):
        """第一次模擬輸出截斷，縮成單筆後回傳有效 JSON。"""
        items = kwargs["payload"]["items"]
        calls.append(len(items))
        if len(items) > 1:
            kwargs["meta_out"]["finish_reason"] = "MAX_TOKENS"
            return ""
        item = items[0]
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "decision": "no_change",
                        "suggested_zh_tw": "",
                        "issue_types": [],
                        "evidence_ids": [],
                        "confidence": 0.8,
                        "reason": "目前譯文可保留。",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=2,
    )

    assert calls == [2, 1, 1]
    assert result.processed_count == result.valid_count == 2
    assert [item.status for item in db.list_smart_review_candidates(result.run_id)] == [
        "no_change",
        "no_change",
    ]


def test_reject_candidate_keeps_translation_unchanged(db, monkeypatch):
    """驗證拒絕候選時會保留原翻譯不變。"""

    def fake_call(**kwargs):
        """模擬模型請求並回傳測試回應。"""
        return json.dumps(
            {
                "items": [
                    {
                        "id": kwargs["payload"]["items"][0]["id"],
                        "decision": "suggest_revision",
                        "suggested_zh_tw": "再見！",
                        "issue_types": ["style"],
                        "evidence_ids": [],
                        "confidence": 0.9,
                        "reason": "原文 Goodbye 與目前譯文 再見 需要人工確認語氣。",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    before = db._q(
        "SELECT zh_tw FROM translation WHERE entry_id=1 AND source=?",
        (SRC_MANUAL,),
    )
    result = run_smart_review(db, model_name="gemini-test", api_key="test-key", limit=1)
    assert db.reject_smart_review_candidate(result.run_id, 1) == "rejected"
    assert db.reject_smart_review_candidate(result.run_id, 1) == "not_ready"
    assert (
        db._q(
            "SELECT zh_tw FROM translation WHERE entry_id=1 AND source=?",
            (SRC_MANUAL,),
        )
        == before
    )


def test_smart_review_snapshot_is_immutable_value_object():
    """驗證智慧校對快照是不可變值物件。"""
    snapshot = SmartReviewSnapshot(
        run_id="",
        ordinal=1,
        entry_id=1,
        kind="lang",
        mc_version="1.21.1",
        mod_id="demo",
        key="hello",
        en_us="Hello",
        current_zh_tw="你好",
        effective_source=0,
        review_status="unreviewed",
        effective_revision=1,
    )
    with pytest.raises((AttributeError, TypeError)):
        snapshot.current_zh_tw = "changed"


def test_smart_review_model_options_use_configured_chatgpt_model():
    """驗證智慧校對模型選項使用設定的 ChatGPT 模型。"""
    from app.services_impl.moddb_smart_review_service import smart_review_model_options

    config = {
        "lm_translator": {
            "provider": "chatgpt",
            "chatgpt_model": "gpt-configured",
            "models": {"gemini-3.1-flash-lite": {"enabled": True}},
        }
    }

    assert smart_review_model_options(config) == [("gpt-configured", "gpt-configured")]


def test_smart_review_model_options_keep_gemini_preference_for_gemini():
    """驗證 Gemini 智慧校對保留 Gemini 模型偏好。"""
    from app.services_impl.moddb_smart_review_service import smart_review_model_options

    config = {
        "lm_translator": {
            "provider": "gemini",
            "chatgpt_model": "gpt-ignored",
            "models": {
                "gemini-3.5-flash-lite": {"enabled": True},
                "gemini-3.1-flash-lite": {"enabled": True},
            },
        }
    }

    assert smart_review_model_options(config) == [
        ("gemini-3.1-flash-lite", "gemini-3.1-flash-lite"),
        ("gemini-3.5-flash-lite", "gemini-3.5-flash-lite"),
    ]


def test_smart_review_service_passes_nested_lm_config(monkeypatch, tmp_path):
    """驗證智慧校對服務會傳遞巢狀 LM 設定。"""
    from app.services_impl import moddb_smart_review_service as service

    config = {"lm_translator": {"request_interval_sec": 0}, "unrelated": True}
    captured = {}

    class FakeDB:
        def close(self):
            """關閉測試用資源。"""
            captured["closed"] = True

    def fake_run(db, **kwargs):
        """模擬智慧校對執行並回傳結果。"""
        captured.update(kwargs)
        assert db is fake_db
        return "result"

    fake_db = FakeDB()
    monkeypatch.setattr(service, "load_config", lambda: config)
    monkeypatch.setattr(service, "get_translation_provider", lambda: "gemini")
    monkeypatch.setattr(service, "_open", lambda *args, **kwargs: fake_db)
    monkeypatch.setattr(service, "run_smart_review", fake_run)

    result = service.run_moddb_smart_review_from_path(
        tmp_path / "db.sqlite", (), model_name="gemini-test", limit=1
    )

    assert result == "result"
    assert captured["lm_config"] == config["lm_translator"]
    assert captured["api_key"] == ""
    assert captured["closed"] is True


def test_smart_review_dialog_builds_preview_and_run_controls(db, monkeypatch):
    """驗證智慧校對對話框建立預覽與執行控制項。"""
    monkeypatch.setattr(
        "app.services_impl.moddb_smart_review_service.load_config",
        lambda: {
            "lm_translator": {
                "provider": "gemini",
                "models": {"gemini-3.1-flash-lite": {"enabled": True}},
            }
        },
    )
    from app.views.moddb.smart_review_dialog import SmartReviewDialog
    from tests.conftest import mock_page

    dialog = SmartReviewDialog(mock_page(), lambda: db, "1.21.1", None)
    assert dialog.dialog.title.value == "AI 智慧校對（候選模式）"
    assert dialog.model_dd.value == "gemini-3.1-flash-lite"
    assert dialog.limit_field.value == "1000"
    assert dialog.run_btn.disabled is False
    assert dialog.recent_run_dd is not None
    assert dialog.refresh_runs_btn.disabled is False


def test_smart_review_candidate_query_is_deferred_and_guarded(monkeypatch, tmp_path):
    """驗證候選 SQLite 查詢排入背景操作且回來時檢查世代與資料庫身分。"""
    from app.views.moddb import smart_review_dialog as module

    class Pager:
        """提供候選載入測試需要的最小分頁介面。"""

        def set_state(self, total, page):
            """保存最近一次分頁狀態。"""
            self.state = (total, page)

    class Container:
        """提供 Flet Column 測試需要的 controls 欄位。"""

        def __init__(self):
            self.controls = []

    path = tmp_path / "smart-review.db"
    db = type("DB", (), {"path": path})()
    dialog = module.SmartReviewDialog.__new__(module.SmartReviewDialog)
    dialog.get_db = lambda: db
    dialog._generation = 3
    dialog._run_id = "run-1"
    dialog._candidate_page = 1
    dialog._candidate_page_size = 50
    dialog._candidate_path = None
    dialog._candidate_db_identity = None
    dialog._candidate_priority = ()
    dialog.candidates = Container()
    dialog.candidate_pager = Pager()
    dialog._safe_update = lambda: None
    dialog._candidate_card = lambda _path, _priority, row: row
    dialog._post = lambda callback, *args: callback(*args)
    scheduled = {}

    def capture_launch(work, *_args, **_kwargs):
        """只捕捉背景工作，不在 UI 呼叫堆疊同步執行。"""
        scheduled["work"] = work

    dialog._launch = capture_launch
    monkeypatch.setattr(
        module,
        "count_moddb_smart_review_candidates_for_run_from_path",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        module,
        "list_moddb_smart_review_candidates_from_path",
        lambda *_args, **_kwargs: [],
    )

    dialog._load_candidates(path, (1,), "run-1")
    assert "work" in scheduled
    scheduled["work"]()
    assert dialog.candidate_pager.state == (0, 1)

    before = list(dialog.candidates.controls)
    dialog._apply_candidates(
        2,
        str(path.resolve()),
        path,
        (1,),
        "run-1",
        1,
        (1, []),
        None,
    )
    assert dialog.candidates.controls == before


def test_smart_review_apply_notifies_parent_after_reloading_candidates(monkeypatch):
    """驗證套用智慧校對後會重新載入候選並通知上層。"""
    from app.views.moddb import smart_review_dialog as module

    dialog = module.SmartReviewDialog.__new__(module.SmartReviewDialog)
    calls = []
    dialog.page = None
    dialog.on_complete = lambda: calls.append("parent")
    dialog._set_busy = lambda busy, message: calls.append((busy, message))
    dialog._load_candidates = lambda *args: calls.append(("reload", args))
    dialog._safe_update = lambda: calls.append("update")
    monkeypatch.setattr(module, "show_snack", lambda *args: None)

    dialog._apply_candidate_result("db", (1,), "run", "applied", None)

    assert calls[1] == ("reload", ("db", (1,), "run"))
    assert calls[2] == "parent"


def test_v7_smart_review_is_readable_and_migrates_to_v8(tmp_path):
    """驗證 v7 智慧校對資料可讀且能遷移到 v8。"""
    path = tmp_path / "v7-smart-review.db"
    writable = TranslationDB(path)
    _seed(writable)
    snapshots = writable.smart_review_candidates(version="1.21.1", limit=1)
    run_id = writable.create_smart_review_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=1,
        batch_size=1,
        scope={"version": "1.21.1", "effective_source": SRC_AI},
    )
    writable.close()

    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE smart_review_run DROP COLUMN scope_json")
    connection.execute("ALTER TABLE smart_review_candidate DROP COLUMN draft_zh_tw")
    connection.execute("UPDATE meta SET value='7' WHERE key='schema_version'")
    connection.commit()
    connection.close()

    readonly = TranslationDB(path, readonly=True)
    old_run = readonly.get_smart_review_run(run_id)
    old_candidate = readonly.list_smart_review_candidates(run_id)[0]
    assert old_run["scope"] == {}
    assert old_candidate.draft_zh_tw is None
    readonly.close()

    migrated = TranslationDB(path)
    assert migrated.get_smart_review_run(run_id)["scope"] == {}
    assert migrated._smart_review_has_column("smart_review_run", "scope_json")
    assert migrated._smart_review_has_column("smart_review_candidate", "draft_zh_tw")
    migrated.close()
    assert list(tmp_path.glob("v7-smart-review.db.pre-schema-v8-*.bak"))


def test_smart_review_ddl_failure_rolls_back_outer_transaction(monkeypatch):
    """驗證智慧校對 DDL 中途失敗不會提交外層 migration。"""
    from translation_tool.translation_db import schema

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entry (id INTEGER PRIMARY KEY)")
    conn.commit()
    original = schema._SMART_REVIEW_DDL
    monkeypatch.setattr(
        schema,
        "_SMART_REVIEW_DDL",
        original[:1] + ("CREATE TABLE broken_smart_review (",) + original[1:],
    )
    conn.execute("BEGIN")
    with pytest.raises(sqlite3.OperationalError):
        schema._install_smart_review_objects(conn)
    conn.rollback()
    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE name='smart_review_run'"
        ).fetchone()
        is None
    )
    conn.close()
