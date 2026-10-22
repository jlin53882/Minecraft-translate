"""智慧校對候選、解析、驗證與套用防護的測試。"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import replace

import pytest
import requests

from translation_tool.translation_db import (
    KIND_ADVANCEMENT,
    KIND_GUIDE,
    KIND_LANG,
    KIND_PATCHOULI,
    ScanItem,
    TranslationDB,
    WriteBackItem,
)
from translation_tool.translation_db import smart_review as smart_review_module
from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.schema import SRC_AI, SRC_JAR_TW, SRC_MANUAL
from translation_tool.translation_db.smart_review import (
    build_smart_review_payload,
    build_smart_review_validation_repair_payload,
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


def _review_response(items) -> str:
    """建立測試用的全量 no_change 校對回應。"""
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
                for item in items
            ]
        }
    )


def _http_error(status: int, message: str = "") -> requests.HTTPError:
    """建立帶安全 provider JSON 的測試 HTTP 錯誤。"""
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(
        {"error": {"message": message, "status": "INVALID_ARGUMENT"}}
    ).encode()
    return requests.HTTPError(response=response)


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


def test_smart_review_scope_options_only_show_unreviewed_modules_and_sources(db):
    """智慧校對範圍選單只列未審核候選，且來源會隨模組分組。"""
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "jar_mod", "jar", "Jar", "罐")],
        fill_other_versions=False,
    )
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "jar_mod",
                "jar_source",
                "Jar source",
                "來源",
                source=SRC_JAR_TW,
            )
        ],
    )
    reviewed = next(
        row
        for row in db.list_entries("1.21.1")[0]
        if row.mod_id == "jar_mod" and row.key == "jar"
    )
    db.review_manual(reviewed.id, expected_zh_tw="罐", propagate=False)

    modules, sources_by_mod = db.smart_review_scope_options(
        version="1.21.1", kind=KIND_LANG
    )

    assert modules == ("demo", "jar_mod")
    assert sources_by_mod["demo"] == (SRC_AI,)
    assert sources_by_mod["jar_mod"] == (SRC_JAR_TW,)
    assert sources_by_mod[None] == (SRC_AI, SRC_JAR_TW)


def test_smart_review_limit_zero_selects_all_candidates(db):
    """驗證智慧校對上限 0 代表不限，而不是選取 0 筆。"""
    snapshots = db.smart_review_candidates(version="1.21.1", limit=0)

    assert len(snapshots) == db.count_smart_review_candidates(version="1.21.1")
    assert len(snapshots) == 2


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


def test_local_context_includes_shared_official_compound_terms(db):
    """Smart Review context 使用與原本機翻相同的官方術語 resolver。"""
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(
                KIND_PATCHOULI,
                "demo",
                "custom.obtain",
                "Obtain a Netherite-Diamond Block",
                "取得一個獄髓鑽石方塊",
            )
        ],
        fill_other_versions=False,
    )
    entry = next(
        item for item in db.list_entries("1.21.1")[0] if item.key == "custom.obtain"
    )

    context = db._smart_review_context(
        entry.id,
        KIND_PATCHOULI,
        "1.21.1",
        "demo",
        entry.key,
        entry.en_us,
    )
    official = [item for item in context if item["reference_type"] == "official_locale"]
    pairs = {(item["source_text"], item["target_text"]) for item in official}

    assert {("Netherite", "獄髓"), ("Diamond", "鑽石"), ("Block", "方塊")} <= pairs
    assert all(item["id"].startswith("official-term:") for item in official)


def test_local_context_marks_unreviewed_module_locale_as_low_confidence(db):
    """未審核模組 zh_tw 只能作參考，不能冒充官方 context。"""
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(
                KIND_LANG,
                "demo",
                "module.term",
                "Module-only phrase",
                "模組自帶譯文",
            )
        ],
        fill_other_versions=False,
        source=SRC_JAR_TW,
    )
    entry = next(
        item for item in db.list_entries("1.21.1")[0] if item.key == "module.term"
    )

    context = db._smart_review_context(
        entry.id,
        KIND_LANG,
        "1.21.1",
        "demo",
        entry.key,
        entry.en_us,
    )
    module = [item for item in context if item["reference_type"] == "module_locale"]

    assert module
    assert module[0]["confidence"] == "low"
    assert "不是官方標準" in module[0]["limitations"]
    assert not [item for item in module if item["reference_type"] == "official_locale"]


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


def test_smart_review_context_prioritizes_current_version_and_text_before_limit(db):
    """驗證同鍵 context 在 LIMIT 前先選目前版本與相同英文且品質合格資料。"""
    versions = [f"1.{index}" for index in range(30)] + ["1.21.1"]
    db.write_back(
        versions[0],
        [WriteBackItem(KIND_LANG, "demo", "shared", "Old 0", "舊 0")],
        fill_other_versions=False,
    )
    for index, version in enumerate(versions[1:], 1):
        db.write_back(
            version,
            [
                WriteBackItem(
                    KIND_LANG,
                    "demo",
                    "shared",
                    "Current text" if version == "1.21.1" else f"Old {index}",
                    "目前文字" if version == "1.21.1" else f"舊 {index}",
                )
            ],
            fill_other_versions=False,
        )
    for version in versions:
        rows, _total = db.list_entries(version)
        for row in rows:
            if row.key == "shared":
                db.review_manual(row.id, expected_zh_tw=row.zh_tw, propagate=False)

    context = db._smart_review_context(
        0, KIND_LANG, "1.21.1", "demo", "shared", "Current text"
    )
    same_key = [
        item for item in context if item["reference_type"] == "same_key_reviewed"
    ]
    assert same_key
    assert same_key[0]["mc_version"] == "1.21.1"
    assert same_key[0]["en_us"] == "Current text"


def test_smart_review_context_cache_separates_version_and_source_text(db):
    """驗證相同 key 的不同版本與英文原文不共用錯誤 evidence cache。"""
    db.write_back(
        "1.20.1",
        [WriteBackItem(KIND_LANG, "demo", "shared", "Old text", "舊文字")],
        fill_other_versions=False,
    )
    db.write_back(
        "1.20.2",
        [WriteBackItem(KIND_LANG, "demo", "shared", "New text", "新文字")],
        fill_other_versions=False,
    )
    rows_1201, _ = db.list_entries("1.20.1")
    rows_1202, _ = db.list_entries("1.20.2")
    db.review_manual(
        next(row for row in rows_1201 if row.key == "shared").id,
        expected_zh_tw="舊文字",
        propagate=False,
    )
    db.review_manual(
        next(row for row in rows_1202 if row.key == "shared").id,
        expected_zh_tw="新文字",
        propagate=False,
    )

    db._smart_review_context_cache = {}
    first = db._smart_review_context(
        0, KIND_LANG, "1.20.1", "demo", "shared", "Old text"
    )
    second = db._smart_review_context(
        0, KIND_LANG, "1.20.2", "demo", "shared", "New text"
    )

    first_same_key = [
        item for item in first if item["reference_type"] == "same_key_reviewed"
    ]
    second_same_key = [
        item for item in second if item["reference_type"] == "same_key_reviewed"
    ]
    assert first_same_key[0]["mc_version"] == "1.20.1"
    assert first_same_key[0]["en_us"] == "Old text"
    assert second_same_key[0]["mc_version"] == "1.20.2"
    assert second_same_key[0]["en_us"] == "New text"


def test_smart_review_glossary_index_keeps_all_source_terms(db):
    """驗證倒排索引會保留同一句中所有獨立 glossary 術語。"""
    db.write_back(
        "1.21.1",
        [
            WriteBackItem(
                KIND_LANG,
                "demo",
                "solar_coil",
                "Solar-Coil",
                "太陽線圈",
            ),
            WriteBackItem(
                KIND_LANG,
                "demo",
                "quantum_valve",
                "Quantum-Valve",
                "量子閥",
            ),
            WriteBackItem(
                KIND_PATCHOULI,
                "demo",
                "install_components",
                "Install Solar Coil beside Quantum Valve",
                "在量子閥旁安裝太陽線圈",
            ),
        ],
        fill_other_versions=False,
    )
    rows, _total = db.list_entries("1.21.1")
    for key in ("solar_coil", "quantum_valve"):
        row = next(item for item in rows if item.key == key)
        db.review_manual(row.id, expected_zh_tw=row.zh_tw, propagate=False)

    candidate = next(item for item in rows if item.key == "install_components")
    context = db._smart_review_context(
        candidate.id,
        KIND_PATCHOULI,
        "1.21.1",
        "demo",
        candidate.key,
        candidate.en_us,
    )
    glossary_pairs = {
        (item["en_us"], item["zh_tw"])
        for item in context
        if item["reference_type"] == "reviewed_lang_glossary"
    }

    assert {
        ("Solar Coil", "太陽線圈"),
        ("Quantum Valve", "量子閥"),
    } <= glossary_pairs


def test_smart_review_glossary_target_beyond_bulk_limit_is_retrievable(db):
    """驗證 id 超過 5000 的 reviewed glossary term 仍可透過 bounded exact lookup 找到。"""
    rows = [
        WriteBackItem(
            KIND_LANG,
            "demo",
            f"term_{index}",
            f"Term {index}",
            f"詞語 {index}",
        )
        for index in range(5000)
    ]
    rows.extend(
        [
            WriteBackItem(
                KIND_LANG,
                "demo",
                "target_term",
                "Target Term",
                "目標術語",
            ),
            WriteBackItem(
                KIND_PATCHOULI,
                "demo",
                "target_page",
                "Target Term is useful",
                "目標術語很有用",
            ),
        ]
    )
    db.write_back("1.21.1", rows, fill_other_versions=False)
    target = next(
        row for row in db.list_entries("1.21.1")[0] if row.key == "target_term"
    )
    db.review_manual(target.id, expected_zh_tw="目標術語", propagate=False)

    candidate = db.smart_review_candidates(
        version="1.21.1",
        mod_id="demo",
        kind=KIND_PATCHOULI,
        effective_source=SRC_AI,
        limit=1,
    )[0]
    glossary = [
        item
        for item in candidate.context
        if item["reference_type"] == "reviewed_lang_glossary"
    ]
    assert any(
        item["en_us"] == "Target Term" and item["zh_tw"] == "目標術語"
        for item in glossary
    )


def test_smart_review_candidate_offset_keeps_absolute_ordinals(db):
    """驗證候選 offset 分頁回傳的 ordinal 是全域序號而非頁內序號。"""
    page = db.smart_review_candidates(version="1.21.1", limit=1, offset=1)
    assert [item.ordinal for item in page] == [2]


def test_smart_review_latest_marks_accepts_nonzero_ordinal_chunks(db):
    """驗證 offset 產生的非 1 起始分片仍可通過嚴格完整性檢查。"""
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
    )

    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "demo", "later", "Later", "稍後")],
        fill_other_versions=False,
    )
    snapshots = db.smart_review_candidates(version="1.21.1", limit=2, offset=1)
    assert [item.ordinal for item in snapshots] == [2, 3]
    store = SmartReviewHistoryStore(db.path, storage_chunk_size=1)
    run_id = store.create_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=2,
        batch_size=1,
    )

    assert store.get_run(run_id)["storage_format"] == "chunked_json_v1"
    selected_run, marks = store.latest_run_marks()

    assert selected_run == run_id
    assert set(marks) == {item.entry_id for item in snapshots}


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
    from translation_tool.translation_db.smart_review_history import (
        history_directory_for_db,
    )

    history_files = list(history_directory_for_db(db.path).glob("*.json"))
    assert len(history_files) == 1
    assert (
        json.loads(history_files[0].read_text(encoding="utf-8"))["id"] == result.run_id
    )
    assert db._q("SELECT COUNT(*) FROM smart_review_run")[0][0] == 0
    assert db._q("SELECT COUNT(*) FROM smart_review_candidate")[0][0] == 0


def test_smart_review_candidate_page_reads_json_once_for_total_and_row(db, monkeypatch):
    """驗證審查器換筆時可用一次 JSON 讀取取得總數與候選。"""
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
    )

    snapshots = [
        SmartReviewSnapshot(
            run_id="",
            ordinal=ordinal,
            entry_id=ordinal,
            kind=KIND_LANG,
            mc_version="1.21.1",
            mod_id="demo",
            key=f"key{ordinal}",
            en_us=f"Text {ordinal}",
            current_zh_tw=f"文字 {ordinal}",
            effective_source=SRC_AI,
            review_status="unreviewed",
            effective_revision=1,
        )
        for ordinal in range(1, 4)
    ]
    store = SmartReviewHistoryStore(db.path)
    run_id = store.create_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=3,
        batch_size=3,
    )
    reads = 0
    original_read = SmartReviewHistoryStore._read_unlocked

    def counted_read(history, value):
        """計算同一頁查詢實際讀取批次 JSON 的次數。"""
        nonlocal reads
        reads += 1
        return original_read(history, value)

    monkeypatch.setattr(SmartReviewHistoryStore, "_read_unlocked", counted_read)
    total, rows = db.load_smart_review_candidate_page(
        run_id,
        limit=1,
        offset=1,
    )

    assert total == 3
    assert [row.ordinal for row in rows] == [2]
    assert reads == 1


def test_smart_review_large_history_uses_manifest_and_partial_chunk_updates(
    db, monkeypatch
):
    """大型批次只更新受影響分片，讀取單筆時不解析整個候選 JSON。"""
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
        history_directory_for_db,
    )

    snapshots = db.smart_review_candidates(version="1.21.1", limit=2)
    store = SmartReviewHistoryStore(db.path, storage_chunk_size=1)
    run_id = store.create_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=2,
        batch_size=1,
    )
    history_dir = history_directory_for_db(db.path)
    manifest_path = history_dir / f"{run_id}.json"
    chunk_dir = history_dir / f"{run_id}.chunks"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["storage_format"] == "chunked_json_v1"
    assert manifest["chunk_count"] == 2
    assert sorted(path.name for path in chunk_dir.glob("*.json")) == [
        "000000.json",
        "000001.json",
    ]

    read_paths = []
    original_read = SmartReviewHistoryStore._read_json_path

    def counted_read(path):
        """記錄單筆讀取實際碰到的 manifest 與分片。"""
        read_paths.append(path.name)
        return original_read(path)

    monkeypatch.setattr(
        SmartReviewHistoryStore, "_read_json_path", staticmethod(counted_read)
    )
    candidate = store.get_candidate(run_id, snapshots[1].ordinal)
    assert candidate is not None
    assert read_paths == [f"{run_id}.json", "000001.json"]

    write_paths = []
    original_write = SmartReviewHistoryStore._write_json_path_unlocked

    def counted_write(history, path, payload):
        """記錄一次結果 checkpoint 寫入哪些 JSON 檔。"""
        write_paths.append(path.name)
        return original_write(history, path, payload)

    monkeypatch.setattr(
        SmartReviewHistoryStore, "_write_json_path_unlocked", counted_write
    )
    store.record_results(
        run_id,
        [
            {
                "id": str(snapshots[0].ordinal),
                "decision": "no_change",
                "suggested_zh_tw": "",
                "issue_types": [],
                "evidence_ids": [],
                "confidence": 0.8,
                "reason": "原文與目前譯文可核對一致。",
            }
        ],
    )
    assert "000000.json" in write_paths
    assert "000001.json" not in write_paths
    assert store.count_candidates(run_id, status="no_change") == 1
    assert store.count_candidates(run_id, status="pending") == 1


def test_smart_review_chunked_create_publishes_manifest_last(db, monkeypatch):
    """建立大型批次失敗時，不應發布缺少分片的 manifest。"""
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
        history_directory_for_db,
    )

    snapshots = db.smart_review_candidates(version="1.21.1", limit=2)
    store = SmartReviewHistoryStore(db.path, storage_chunk_size=1)
    original_write = SmartReviewHistoryStore._write_chunk_path_unlocked

    def fail_second_chunk(history, path, payload):
        """模擬第二個暫存分片寫入失敗。"""
        if path.name == "000001.json":
            raise OSError("simulated chunk write failure")
        return original_write(history, path, payload)

    monkeypatch.setattr(
        SmartReviewHistoryStore, "_write_chunk_path_unlocked", fail_second_chunk
    )
    with pytest.raises(OSError, match="simulated chunk write failure"):
        store.create_run(
            snapshots,
            provider="gemini",
            model="test-model",
            version="1.21.1",
            requested_limit=2,
            batch_size=1,
        )

    history_dir = history_directory_for_db(db.path)
    assert list(history_dir.glob("*.json")) == []
    assert list(history_dir.glob("*.chunks")) == []


def test_smart_review_chunk_manifest_recovers_after_manifest_write_failure(
    db, monkeypatch
):
    """分片已寫入但 manifest checkpoint 失敗時，下一次讀取應重建摘要。"""
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
    )

    snapshots = db.smart_review_candidates(version="1.21.1", limit=2)
    store = SmartReviewHistoryStore(db.path, storage_chunk_size=1)
    run_id = store.create_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=2,
        batch_size=1,
    )
    original_write = SmartReviewHistoryStore._write_unlocked
    fail_once = True

    def fail_manifest_once(history, value, payload):
        """只讓第一次結果 manifest 寫入失敗。"""
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise OSError("simulated manifest write failure")
        return original_write(history, value, payload)

    monkeypatch.setattr(SmartReviewHistoryStore, "_write_unlocked", fail_manifest_once)
    with pytest.raises(OSError, match="simulated manifest write failure"):
        store.record_results(
            run_id,
            [
                {
                    "id": str(snapshots[0].ordinal),
                    "decision": "no_change",
                    "suggested_zh_tw": "",
                    "issue_types": [],
                    "evidence_ids": [],
                    "confidence": 0.8,
                    "reason": "原文與目前譯文可核對一致。",
                }
            ],
        )

    assert store.count_candidates(run_id, status="no_change") == 1
    assert store.count_candidates(run_id, status="pending") == 1


def test_smart_review_ai_metrics_survive_manual_status_changes(db):
    """人工套用或拒絕候選時，AI 執行統計不可被目前狀態倒扣。"""
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
    )

    snapshots = db.smart_review_candidates(version="1.21.1", limit=2)
    store = SmartReviewHistoryStore(db.path, storage_chunk_size=1)
    run_id = store.create_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=2,
        batch_size=1,
    )
    store.record_results(
        run_id,
        [
            {
                "id": str(snapshots[0].ordinal),
                "decision": "no_change",
                "suggested_zh_tw": "",
                "issue_types": [],
                "evidence_ids": [],
                "confidence": 0.8,
                "reason": "原文與目前譯文可核對一致。",
            }
        ],
    )
    assert store.get_run(run_id)["processed_count"] == 1
    assert store.get_run(run_id)["valid_count"] == 1

    assert store.update_candidate(run_id, snapshots[0].ordinal, status="applied")
    assert store.update_candidate(run_id, snapshots[0].ordinal, status="rejected")
    summary = store.get_run(run_id)
    assert summary["processed_count"] == 1
    assert summary["valid_count"] == 1


def test_smart_review_page_skips_unneeded_chunks_for_absolute_offset(db, monkeypatch):
    """未指定狀態時，分頁 offset 應直接定位分片而非從第一片掃描。"""
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
    )

    first = db.smart_review_candidates(version="1.21.1", limit=1)[0]
    snapshots = [
        first,
        replace(first, ordinal=2, entry_id=2, key="second"),
        replace(first, ordinal=3, entry_id=3, key="third"),
    ]
    store = SmartReviewHistoryStore(db.path, storage_chunk_size=1)
    run_id = store.create_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=3,
        batch_size=1,
    )
    original_read = SmartReviewHistoryStore._read_chunk_unlocked
    read_indexes = []

    def counted_chunk_read(history, value, index):
        """記錄分頁實際開啟的候選分片。"""
        read_indexes.append(index)
        return original_read(history, value, index)

    monkeypatch.setattr(
        SmartReviewHistoryStore, "_read_chunk_unlocked", counted_chunk_read
    )
    total, rows = store.page_candidates(run_id, offset=2, limit=1)
    assert total == 3
    assert [row.ordinal for row in rows] == [3]
    assert read_indexes == [2]


def test_smart_review_reconcile_reads_only_target_chunks(db, monkeypatch):
    """對帳只應讀取 audit marker 指向的分片。"""
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
    )

    first = db.smart_review_candidates(version="1.21.1", limit=1)[0]
    snapshots = [
        first,
        replace(first, ordinal=2, entry_id=2, key="second"),
        replace(first, ordinal=3, entry_id=3, key="third"),
    ]
    store = SmartReviewHistoryStore(db.path, storage_chunk_size=1)
    run_id = store.create_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=3,
        batch_size=1,
    )
    original_read = SmartReviewHistoryStore._read_chunk_unlocked
    read_indexes = []

    def counted_chunk_read(history, value, index):
        """記錄對帳實際開啟的候選分片。"""
        read_indexes.append(index)
        return original_read(history, value, index)

    monkeypatch.setattr(
        SmartReviewHistoryStore, "_read_chunk_unlocked", counted_chunk_read
    )
    assert (
        store.reconcile_applied(
            run_id,
            {3: {"entry_id": 3, "batch": "batch-3", "applied_at": "now"}},
        )
        == 1
    )
    assert read_indexes == [2]


@pytest.mark.parametrize(
    "corruption",
    ["missing", "invalid_json", "wrong_candidates_type", "incomplete_candidates"],
)
def test_smart_review_latest_marks_falls_back_from_corrupt_chunk(db, corruption):
    """最近批次的分片損壞時，條目狀態應回退到較早的完整批次。"""
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
        history_directory_for_db,
    )

    snapshots = db.smart_review_candidates(version="1.21.1", limit=2)
    store = SmartReviewHistoryStore(db.path, storage_chunk_size=1)

    older_run = store.create_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=2,
        batch_size=1,
    )
    store.record_results(
        older_run,
        [
            {
                "id": str(item.ordinal),
                "decision": "no_change",
                "suggested_zh_tw": "",
                "issue_types": [],
                "evidence_ids": [],
                "confidence": 0.8,
                "reason": "原文與目前譯文可核對一致。",
            }
            for item in snapshots
        ],
    )
    latest_run = store.create_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=2,
        batch_size=1,
    )
    chunk_path = (
        history_directory_for_db(db.path) / f"{latest_run}.chunks" / "000000.json"
    )
    if corruption == "missing":
        chunk_path.unlink()
    elif corruption == "invalid_json":
        chunk_path.write_text("{", encoding="utf-8")
    elif corruption == "wrong_candidates_type":
        chunk_path.write_text(json.dumps({"candidates": {}}), encoding="utf-8")
    else:
        chunk_payload = json.loads(chunk_path.read_text(encoding="utf-8"))
        chunk_payload["candidates"] = []
        chunk_path.write_text(json.dumps(chunk_payload), encoding="utf-8")

    selected_run, marks = store.latest_run_marks()
    assert selected_run == older_run
    assert len(marks) == len(snapshots)


def test_smart_review_latest_marks_invalidates_chunk_cache_after_corruption(db):
    """最近標籤快取命中後，分片損壞或修復仍應重新判斷批次。"""
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
        history_directory_for_db,
    )

    snapshots = db.smart_review_candidates(version="1.21.1", limit=2)
    store = SmartReviewHistoryStore(db.path, storage_chunk_size=1)
    run_id = store.create_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=2,
        batch_size=1,
    )
    store.record_results(
        run_id,
        [
            {
                "id": str(snapshots[0].ordinal),
                "decision": "no_change",
                "suggested_zh_tw": "",
                "issue_types": [],
                "evidence_ids": [],
                "confidence": 0.8,
                "reason": "原文與目前譯文可核對一致。",
            }
        ],
    )
    selected_run, marks = store.latest_run_marks()
    assert selected_run == run_id
    assert marks[snapshots[0].entry_id][0] == "no_change"

    chunk_path = history_directory_for_db(db.path) / f"{run_id}.chunks" / "000000.json"
    original_payload = json.loads(chunk_path.read_text(encoding="utf-8"))
    broken_payload = dict(original_payload)
    broken_payload["candidates"] = []
    chunk_path.write_text(json.dumps(broken_payload), encoding="utf-8")
    assert store.latest_run_marks() == (None, {})

    chunk_path.write_text(json.dumps(original_payload), encoding="utf-8")
    selected_run, marks = store.latest_run_marks()
    assert selected_run == run_id
    assert marks[snapshots[0].entry_id][0] == "no_change"


def test_smart_review_run_history_merges_legacy_pages_without_double_offset(db):
    """驗證合併 JSON／SQLite 歷史後只套用一次 offset，且可超過 100 筆。"""
    rows = []
    for index in range(125):
        day = index // 24 + 1
        hour = index % 24
        rows.append(
            (
                f"legacy-{index:03d}",
                "gemini",
                "test-model",
                "1.21.1",
                1,
                1,
                "{}",
                f"2026-01-{day:02d} {hour:02d}:00:00",
                f"2026-01-{day:02d} {hour:02d}:00:01",
                "completed",
                1,
                1,
                1,
                0,
                0,
                "{}",
            )
        )
    with db._tx(bump_generation_on_noop=False) as conn:
        conn.executemany(
            "INSERT INTO smart_review_run ("
            "id, provider, model, scope_version, requested_limit, batch_size, "
            "scope_json, started_at, finished_at, status, selected_count, "
            "processed_count, valid_count, invalid_count, request_error_count, metrics"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )

    page = db.list_smart_review_runs(limit=20, offset=100)
    assert len(page) == 20
    assert len({run["id"] for run in page}) == 20
    assert page[0]["id"] == "legacy-024"
    assert db.list_smart_review_runs(limit=2, offset=2)[0]["id"] == "legacy-122"


def test_smart_review_run_history_keeps_json_mtime_tie_breaker(db):
    """同秒的 JSON 與 SQLite 批次合併時，沿用 JSON 檔案的時間排序。"""
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
    )

    started_at = "2026-01-01 00:00:00"
    history = SmartReviewHistoryStore(db.path)
    history.directory.mkdir(parents=True, exist_ok=True)
    history.path_for("aaa-json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "aaa-json",
                "provider": "gemini",
                "model": "test-model",
                "scope_version": 1,
                "scope": {},
                "started_at": started_at,
                "finished_at": started_at,
                "status": "completed",
                "selected_count": 0,
                "processed_count": 0,
                "valid_count": 0,
                "invalid_count": 0,
                "request_error_count": 0,
                "metrics": {},
                "candidates": [],
            }
        ),
        encoding="utf-8",
    )
    with db._tx(bump_generation_on_noop=False) as conn:
        conn.execute(
            "INSERT INTO smart_review_run ("
            "id, provider, model, scope_version, requested_limit, batch_size, "
            "scope_json, started_at, finished_at, status, selected_count, "
            "processed_count, valid_count, invalid_count, request_error_count, metrics"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "zzz-legacy",
                "gemini",
                "test-model",
                "1.21.1",
                1,
                1,
                "{}",
                started_at,
                started_at,
                "completed",
                0,
                0,
                0,
                0,
                0,
                "{}",
            ),
        )

    page = db.list_smart_review_runs(limit=2)
    assert [run["id"] for run in page] == ["aaa-json", "zzz-legacy"]
    assert "_history_mtime_ns" not in page[0]


def test_json_apply_reconciles_sqlite_commit_when_history_write_fails(db, monkeypatch):
    """SQLite 已提交但 JSON 回寫失敗時，下一次讀取會安全標記 applied。"""

    def fake_call(**kwargs):
        """建立可供套用的建議回應。"""
        item = kwargs["payload"]["items"][0]
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "decision": "suggest_revision",
                        "suggested_zh_tw": f"{item['current_translation']}（修訂）",
                        "issue_types": ["style"],
                        "evidence_ids": [],
                        "confidence": 0.9,
                        "reason": (
                            f"原文 {item['source_text']} 與目前譯文 "
                            f"{item['current_translation']} 的語氣需要人工確認。"
                        ),
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    result = run_smart_review(db, model_name="gemini-test", api_key="test-key", limit=1)
    candidate = db.list_smart_review_candidates(result.run_id)[0]
    assert candidate.status == "ready", candidate
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
    )

    original_update = SmartReviewHistoryStore.update_candidate

    def fail_after_sqlite_commit(self, run_id, ordinal, **updates):
        """模擬 SQLite 套用完成後的 JSON 原子替換失敗。"""
        if updates.get("status") == "applied":
            raise OSError("simulated history write failure")
        return original_update(self, run_id, ordinal, **updates)

    monkeypatch.setattr(
        SmartReviewHistoryStore, "update_candidate", fail_after_sqlite_commit
    )
    assert (
        db.apply_smart_review_candidate(result.run_id, candidate.ordinal) == "applied"
    )
    assert (
        db.entry_detail(candidate.entry_id).entry.zh_tw
        == f"{candidate.current_zh_tw}（修訂）"
    )
    assert (
        SmartReviewHistoryStore(db.path)
        .get_candidate(result.run_id, candidate.ordinal)
        .get("status")
        == "ready"
    )

    monkeypatch.setattr(SmartReviewHistoryStore, "update_candidate", original_update)
    reconciled = db.list_smart_review_candidates(result.run_id)[0]
    assert reconciled.status == "applied"


def test_smart_review_reports_search_and_api_timing(db, monkeypatch):
    """驗證進度與持久化 metrics 能區分搜尋、API 與批次處理耗時。"""
    events = []
    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        lambda **kwargs: _review_response(kwargs["payload"]["items"]),
    )

    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=1,
        progress=events.append,
    )

    assert events[0]["phase"] == "selection_complete"
    assert "selection_seconds" in events[0]["timing"]
    batch_events = [event for event in events if event["phase"] == "batch_complete"]
    assert len(batch_events) == 2
    assert {"api_seconds", "parse_seconds", "result_write_seconds"} <= set(
        batch_events[0]["timing"]
    )
    assert result.metrics["selection_seconds"] >= 0
    assert result.metrics["api_seconds"] >= 0
    assert result.metrics["batch_count"] == 2


def test_smart_review_batch_event_reports_validation_distribution(db, monkeypatch):
    """批次事件與持久化 metrics 都保留 validation error 分布。"""
    events = []

    def invalid_response(**kwargs):
        """回傳 id 正確但缺少 reason 的候選，模擬模型格式錯誤。"""
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
                    }
                    for item in kwargs["payload"]["items"]
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        invalid_response,
    )
    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=2,
        progress=events.append,
        lm_config={"rate_limit": {"sleep_seconds_between_batches": 0}},
    )

    batch_event = next(
        snapshot["event"]
        for snapshot in events
        if snapshot.get("event", {}).get("kind") == "batch_complete"
    )
    assert batch_event["valid_count"] == 0
    assert batch_event["invalid_count"] == 2
    assert batch_event["validation_errors"] == {"item 欄位不符合 strict schema": 2}
    assert result.metrics["validation_errors"] == batch_event["validation_errors"]
    assert result.metrics["suggest_revision_count"] == 0
    assert result.metrics["validation_repair_attempts"] == 1
    assert result.metrics["validation_repair_items"] == 2
    assert result.metrics["repaired_valid_count"] == 0
    assert result.metrics["repaired_invalid_count"] == 2


def test_smart_review_retries_only_invalid_items_and_revalidates(db, monkeypatch):
    """驗證 invalid 條目只修復一次，且修復後仍須通過完整 strict validation。"""
    calls: list[dict] = []
    events: list[dict] = []

    def response(**kwargs):
        """第一次製造缺少 reason，第二次只修復送入的 invalid 條目。"""
        payload = kwargs["payload"]
        calls.append(payload)
        if "validation_errors" in payload:
            return _review_response(payload["items"])
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
                    }
                    for item in payload["items"]
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        response,
    )
    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=2,
        progress=events.append,
        lm_config={"rate_limit": {"sleep_seconds_between_batches": 0}},
    )

    assert len(calls) == 2
    assert [item["id"] for item in calls[1]["items"]] == [
        item["id"] for item in calls[0]["items"]
    ]
    assert result.valid_count == 2
    assert result.invalid_count == 0
    assert result.metrics["validation_repair_attempts"] == 1
    assert result.metrics["repaired_valid_count"] == 2
    assert result.metrics["repaired_invalid_count"] == 0
    kinds = {snapshot["event"]["kind"] for snapshot in events if snapshot.get("event")}
    assert {
        "validation_repair_start",
        "validation_repair_attempt_start",
        "validation_repair_attempt_success",
        "validation_repair_complete",
    } <= kinds
    repair_events = [
        snapshot["event"]
        for snapshot in events
        if snapshot.get("event", {}).get("kind", "").startswith("validation_repair")
    ]
    assert {event["phase"] for event in repair_events} >= {"validation", "api"}
    assert all(
        item.status == "no_change"
        for item in db.list_smart_review_candidates(result.run_id)
    )


def test_smart_review_repair_payload_is_bounded_to_invalid_results(db):
    """驗證修復 payload 不會重送已通過的條目或完整模型回應。"""
    snapshots = db.smart_review_candidates(version="1.21.1", limit=2)
    payload = build_smart_review_validation_repair_payload(
        snapshots,
        [
            {"id": str(snapshots[0].ordinal), "validation_error": "reason 不可為空"},
            {"id": str(snapshots[1].ordinal), "decision": "no_change"},
        ],
    )
    assert [item["id"] for item in payload["items"]] == [str(snapshots[0].ordinal)]
    assert payload["validation_errors"] == [
        {"id": str(snapshots[0].ordinal), "error": "reason 不可為空"}
    ]
    assert "response" not in payload


def test_smart_review_clamps_excessive_batch_size(db, monkeypatch):
    """驗證服務層會將過大的批次限制在可觀測性測試採用的安全上限。"""
    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        lambda **kwargs: _review_response(kwargs["payload"]["items"]),
    )
    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=100,
    )
    assert result.metrics["batch_count"] == 1
    assert db.get_smart_review_run(result.run_id)["batch_size"] == 50


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


def test_smart_review_cancel_after_batch_keeps_completed_json_checkpoint(
    db, monkeypatch
):
    """驗證批次中途取消會保留已完成結果，並把剩餘候選標記為取消。"""
    cancel_requested = False

    def fake_call(**kwargs):
        """第一批回應完成後模擬使用者按下停止。"""
        nonlocal cancel_requested
        cancel_requested = True
        return _review_response(kwargs["payload"]["items"])

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=1,
        cancel_check=lambda: cancel_requested,
    )

    assert result.status == "cancelled"
    assert result.processed_count == 1
    assert [item.status for item in db.list_smart_review_candidates(result.run_id)] == [
        "no_change",
        "cancelled",
    ]
    from translation_tool.translation_db.smart_review_history import (
        history_directory_for_db,
    )

    history_path = history_directory_for_db(db.path) / f"{result.run_id}.json"
    payload = json.loads(history_path.read_text(encoding="utf-8"))
    assert payload["status"] == "cancelled"
    assert payload["processed_count"] == 1
    assert [candidate["status"] for candidate in payload["candidates"]] == [
        "no_change",
        "cancelled",
    ]


def test_smart_review_recovers_dead_owner_without_losing_completed_candidates(
    db, monkeypatch
):
    """驗證意外退出後下一次讀取會收斂 running JSON，保留已完成候選。"""
    from translation_tool.translation_db import smart_review_history
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
    )

    snapshots = db.smart_review_candidates(version="1.21.1", limit=2)
    store = SmartReviewHistoryStore(db.path)
    run_id = store.create_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=2,
        batch_size=1,
    )
    store.record_results(
        run_id,
        [
            {
                "id": str(snapshots[0].ordinal),
                "decision": "no_change",
                "suggested_zh_tw": "",
                "issue_types": [],
                "evidence_ids": [],
                "confidence": 0.8,
                "reason": "原文與目前譯文可核對一致。",
            }
        ],
    )
    path = store.path_for(run_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["owner_pid"] = 999999
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(smart_review_history, "_process_is_alive", lambda _pid: False)
    SmartReviewHistoryStore._recovery_checked.discard(store.directory)

    run = db.get_smart_review_run(run_id)
    assert run is not None
    assert run["status"] == "interrupted"
    assert run["processed_count"] == 1
    assert run["metrics"]["recovered_after_unexpected_exit"] is True
    assert run["metrics"]["interrupted_pending_count"] == 1
    assert [item.status for item in db.list_smart_review_candidates(run_id)] == [
        "no_change",
        "pending",
    ]


def test_smart_review_does_not_recover_live_owner(db, monkeypatch):
    """驗證仍由目前程序執行的 running 批次不會被誤標為中斷。"""
    from translation_tool.translation_db.smart_review_history import (
        SmartReviewHistoryStore,
    )

    snapshots = db.smart_review_candidates(version="1.21.1", limit=1)
    store = SmartReviewHistoryStore(db.path)
    run_id = store.create_run(
        snapshots,
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=1,
        batch_size=1,
    )
    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review_history._process_is_alive",
        lambda _pid: True,
    )

    assert SmartReviewHistoryStore(db.path).recover_abandoned_runs(force=True) == []
    assert db.get_smart_review_run(run_id)["status"] == "running"


def test_smart_review_retries_gemini_503_with_bounded_shared_backoff(db, monkeypatch):
    """驗證 Gemini 503 使用共用 policy 重試，成功後只保存一次結果。"""
    calls = []

    def fake_call(**kwargs):
        """第一次回傳 503，下一次回傳有效結果。"""
        calls.append(len(kwargs["payload"]["items"]))
        if len(calls) == 1:
            raise _http_error(503, "UNAVAILABLE: high demand")
        return _review_response(kwargs["payload"]["items"])

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.interruptible_sleep",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.interruptible_sleep",
        lambda *_args, **_kwargs: None,
    )

    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=1,
        batch_size=1,
    )

    assert calls == [1, 1]
    assert result.status == "completed"
    assert result.metrics["transient_retry_count"] == 1
    assert result.metrics["request_attempts"] == 2


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_smart_review_retry_events_classify_server_errors(db, monkeypatch, status):
    """Gemini 暫時性 HTTP 錯誤會產生 failure、retry wait 與 resume 事件。"""
    calls = []
    events = []

    def fake_call(**kwargs):
        """第一次回傳指定的暫時性 HTTP 錯誤，下一次成功。"""
        calls.append(1)
        if len(calls) == 1:
            error = _http_error(status, "provider overload secret detail")
            error.response.headers["Retry-After"] = "45"
            raise error
        return _review_response(kwargs["payload"]["items"])

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.interruptible_sleep",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.interruptible_sleep",
        lambda *_args, **_kwargs: None,
    )

    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="AIza-secret-key",
        limit=1,
        batch_size=1,
        progress=events.append,
        lm_config={"rate_limit": {"sleep_seconds_between_batches": 0}},
    )

    event_rows = [event["event"] for event in events if "event" in event]
    kinds = [event["kind"] for event in event_rows]
    assert result.status == "completed"
    assert calls == [1, 1]
    assert {
        "batch_start",
        "attempt_start",
        "attempt_failure",
        "retry_wait",
        "retry_resume",
        "attempt_success",
        "batch_complete",
    } <= set(kinds)
    failure = next(event for event in event_rows if event["kind"] == "attempt_failure")
    assert failure["http_status"] == status
    assert failure["provider_overload"] is (status == 503)
    retry = next(event for event in event_rows if event["kind"] == "retry_wait")
    assert retry["wait_seconds"] == 45
    assert result.metrics["retry_wait_seconds"] >= 45
    assert result.metrics["rate_limit_wait_seconds"] < 45
    assert result.metrics["overload_count"] == (1 if status == 503 else 0)
    assert all(
        secret not in repr(event_rows)
        for secret in ("AIza-secret-key", "provider overload secret detail")
    )


def test_smart_review_timeout_events_are_visible_and_safe(db, monkeypatch):
    """timeout 也要顯示分類與 retry wait，且不記錄例外原文。"""
    calls = []
    events = []

    def fake_call(**kwargs):
        """第一次 timeout，第二次回傳有效結果。"""
        calls.append(1)
        if len(calls) == 1:
            raise requests.Timeout("timeout contains private prompt")
        return _review_response(kwargs["payload"]["items"])

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.interruptible_sleep",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.interruptible_sleep",
        lambda *_args, **_kwargs: None,
    )

    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=1,
        batch_size=1,
        progress=events.append,
        lm_config={"rate_limit": {"sleep_seconds_between_batches": 0}},
    )

    event_rows = [event["event"] for event in events if "event" in event]
    failure = next(event for event in event_rows if event["kind"] == "attempt_failure")
    assert failure["error_kind"] == "timeout"
    assert failure["timeout"] is True
    assert result.metrics["transient_retry_count"] == 1
    assert result.metrics["retry_wait_seconds"] > 0
    assert "timeout contains private prompt" not in repr(event_rows)


def test_smart_review_bounds_continuous_gemini_503_and_stops_pending_batches(
    db, monkeypatch
):
    """驗證連續 503 最多重試既有上限，不會無限重打後續批次。"""
    calls = []

    def fail_call(**_kwargs):
        """固定回傳 503。"""
        calls.append(1)
        raise _http_error(503, "UNAVAILABLE")

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fail_call,
    )
    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.interruptible_sleep",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.interruptible_sleep",
        lambda *_args, **_kwargs: None,
    )

    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=1,
    )

    assert len(calls) == 4
    assert result.status == "partial"
    assert result.request_error_count == 1
    assert result.metrics["transient_retry_count"] == 3
    assert all(
        item.status == "request_failed"
        for item in db.list_smart_review_candidates(result.run_id)
    )


def test_smart_review_cancel_during_provider_backoff_is_cooperative(db, monkeypatch):
    """驗證 provider 退避期間取消會停止後續請求。"""
    import threading

    cancelled = threading.Event()
    calls = []

    def fail_call(**_kwargs):
        """第一次請求回傳 503。"""
        calls.append(1)
        raise _http_error(503, "UNAVAILABLE")

    def cancel_during_sleep(*_args, **_kwargs):
        """模擬 UI 在退避等待期間按下停止。"""
        cancelled.set()

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fail_call,
    )
    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.interruptible_sleep",
        cancel_during_sleep,
    )
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.interruptible_sleep",
        cancel_during_sleep,
    )

    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=1,
        batch_size=1,
        cancel_check=cancelled.is_set,
    )

    assert calls == [1]
    assert result.status == "cancelled"
    assert db.list_smart_review_candidates(result.run_id)[0].status == "cancelled"


def test_smart_review_does_not_shrink_invalid_400_schema(db, monkeypatch):
    """驗證沒有大小證據的 400 schema 錯誤不會拆成單筆請求。"""
    calls = []

    def fail_call(**kwargs):
        """回傳 schema 錯誤，記錄實際批次大小。"""
        calls.append(len(kwargs["payload"]["items"]))
        raise _http_error(
            400,
            'Invalid JSON payload received. Unknown name "responseSchema"',
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fail_call,
    )

    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=2,
        batch_size=2,
    )

    assert calls == [2]
    assert result.request_error_count == 1
    assert result.metrics["shrink_count"] == 0
    assert (
        result.metrics["failure_classes"]["request_failed:http_400:invalid_argument"]
        == 1
    )


def test_smart_review_shrinks_explicit_payload_too_large_413(db, monkeypatch):
    """驗證 413 明確代表 payload 過大時才縮小批次。"""
    calls = []

    def fake_call(**kwargs):
        """第一次回傳 413，單筆後回傳有效結果。"""
        items = kwargs["payload"]["items"]
        calls.append(len(items))
        if len(calls) == 1:
            raise _http_error(413, "request payload too large")
        return _review_response(items)

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
    assert result.status == "completed"
    assert result.metrics["shrink_count"] == 1


def test_smart_review_shrinks_400_only_when_context_size_is_explicit(db, monkeypatch):
    """驗證 400 context-too-long 有明確大小證據時才縮批。"""
    calls = []

    def fake_call(**kwargs):
        """第一次回傳 context 過長，單筆後成功。"""
        items = kwargs["payload"]["items"]
        calls.append(len(items))
        if len(calls) == 1:
            raise _http_error(400, "context too long")
        return _review_response(items)

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
    assert result.status == "completed"


def test_smart_review_metrics_count_usage_for_truncated_retry(db, monkeypatch):
    """驗證截斷請求與後續成功重試的 usage 都各計一次。"""
    calls = []

    def fake_call(**kwargs):
        """批次回傳截斷，單筆回傳帶 usage 的成功結果。"""
        items = kwargs["payload"]["items"]
        calls.append(len(items))
        if len(items) > 1:
            kwargs["meta_out"].update(
                {
                    "prompt_tokens": 10,
                    "candidates_tokens": 20,
                    "finish_reason": "MAX_TOKENS",
                }
            )
            return ""
        kwargs["meta_out"].update(
            {"prompt_tokens": 5, "candidates_tokens": 6, "finish_reason": "STOP"}
        )
        return _review_response(items)

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
    assert result.metrics["request_attempts"] == 3
    assert result.metrics["prompt_tokens"] == 20
    assert result.metrics["output_tokens"] == 32
    assert result.metrics["usage_missing_attempts"] == 0
    assert result.metrics["token_counts"] == "provider_reported"


def test_smart_review_enforces_shared_input_token_budget_before_send(db, monkeypatch):
    """驗證超過共用輸入 token 預算時單筆安全失敗且不呼叫 provider。"""
    called = False

    def should_not_call(**_kwargs):
        """預算擋下請求時不應執行。"""
        nonlocal called
        called = True

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        should_not_call,
    )

    result = run_smart_review(
        db,
        model_name="gemini-test",
        api_key="test-key",
        limit=1,
        batch_size=1,
        lm_config={"max_input_token_budget": 1},
    )

    assert not called
    assert result.status == "partial"
    assert result.request_error_count == 1
    assert result.metrics["request_attempts"] == 0
    assert result.metrics["input_budget_exceeded_count"] == 1
    assert (
        db.list_smart_review_candidates(result.run_id)[0].validation_error
        == "request_failed:input_budget_exceeded"
    )


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


def test_smart_review_chatgpt_404_uses_shared_api_error_classification(db, monkeypatch):
    """驗證 ChatGPT 404 使用既有 Responses 錯誤分類且不拆批重打。"""
    from translation_tool.core.openai_codex_client import ChatGPTAPIError

    calls = []

    def fail_call(**_kwargs):
        """模擬找不到模型。"""
        calls.append(1)
        raise ChatGPTAPIError(
            "model_not_found",
            "The model does not exist.",
            status=404,
            error_type="invalid_request_error",
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fail_call,
    )
    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.chatgpt_account_status",
        dict,
    )

    result = run_smart_review(
        db,
        model_name="missing-model",
        provider="chatgpt",
        api_key="",
        limit=2,
        batch_size=2,
    )

    assert calls == [1]
    assert result.status == "partial"
    assert result.request_error_count == 1
    assert any(
        code.startswith("request_failed:chatgpt:404:model_not_found")
        for code in result.metrics["failure_classes"]
    )


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


def test_json_apply_keeps_cas_read_and_write_in_one_transaction(db, monkeypatch):
    """驗證 JSON 候選套用不會覆蓋同時完成的外部人工修改。"""

    def fake_call(**kwargs):
        """模擬可套用的模型回應。"""
        item = kwargs["payload"]["items"][0]
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "decision": "suggest_revision",
                        "suggested_zh_tw": f"{item['current_translation']}（模型建議）",
                        "issue_types": ["style"],
                        "evidence_ids": [],
                        "confidence": 0.95,
                        "reason": (
                            f"原文 {item['source_text']} 與目前譯文 "
                            f"{item['current_translation']} 需要確認人工修改是否仍然有效。"
                        ),
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review.call_gemini_requests",
        fake_call,
    )
    result = run_smart_review(db, model_name="gemini-test", api_key="test-key", limit=1)
    candidate = db.list_smart_review_candidates(result.run_id)[0]
    external = TranslationDB(db.path)
    writer_started = threading.Event()
    writer_finished = threading.Event()

    def write_external_manual_translation():
        """在候選快照讀取後嘗試寫入另一個人工譯文。"""
        writer_started.set()
        with external._tx() as conn:
            conn.execute(
                "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
                "VALUES (?,?,?,'','unreviewed') ON CONFLICT(entry_id, source) DO UPDATE SET "
                "zh_tw=excluded.zh_tw, checker='', review_status='unreviewed', "
                "updated_at=CURRENT_TIMESTAMP",
                (candidate.entry_id, SRC_MANUAL, "外部人工修改"),
            )
        writer_finished.set()

    writer = threading.Thread(target=write_external_manual_translation)
    snapshot_sql = smart_review_module._smart_review_current_snapshot_sql().strip()
    original_connection = db._conn

    class CursorProxy:
        """在 SQLite 游標取回 CAS 快照後啟動外部寫入。"""

        def __init__(self, cursor):
            """保存原始游標與一次性回呼。"""
            self._cursor = cursor
            self._called = False

        def fetchone(self):
            """取回快照後讓另一個連線嘗試修改資料。"""
            row = self._cursor.fetchone()
            if not self._called:
                self._called = True
                writer.start()
                assert writer_started.wait(1)
                # 舊流程沒有交易鎖，外部寫入會在這裡完成；修復後會等
                # 套用交易提交，再由外部連線完成，形成可驗證的差異。
                writer_finished.wait(1.0)
            return row

        def __getattr__(self, name):
            """轉送未攔截的游標操作。"""
            return getattr(self._cursor, name)

    class ConnectionProxy:
        """只攔截 CAS 快照查詢，其餘 SQLite 操作維持原行為。"""

        def execute(self, sql, params=()):
            """執行 SQL，並在指定快照游標讀取後啟動競態測試。"""
            cursor = original_connection.execute(sql, params)
            if str(sql).strip() == snapshot_sql:
                return CursorProxy(cursor)
            return cursor

        def __getattr__(self, name):
            """轉送交易管理所需的其他連線操作。"""
            return getattr(original_connection, name)

    db._conn = ConnectionProxy()
    try:
        assert (
            db.apply_smart_review_candidate(result.run_id, candidate.ordinal)
            == "applied"
        )
        writer.join(timeout=5)
        assert not writer.is_alive()
        assert writer_finished.is_set()
        assert (
            db._q(
                "SELECT zh_tw FROM translation WHERE entry_id=? AND source=?",
                (candidate.entry_id, SRC_MANUAL),
            )[0][0]
            == "外部人工修改"
        )
    finally:
        external.close()


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

    config = {
        "lm_translator": {"request_interval_sec": 0},
        "smart_review": {
            "default_batch_size": 12,
            "max_batch_size": 24,
            "storage_chunk_size": 300,
        },
        "unrelated": True,
    }
    captured = {}

    class FakeDB:
        """可記錄智慧校對呼叫參數的測試資料庫替身。"""

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
    assert captured["batch_size"] == 12
    assert captured["api_key"] == ""
    assert captured["closed"] is True


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
    assert old_run["scope"] == {"version": "1.21.1", "effective_source": SRC_AI}
    assert old_candidate.draft_zh_tw is None
    readonly.close()

    migrated = TranslationDB(path)
    assert migrated.get_smart_review_run(run_id)["scope"] == {
        "version": "1.21.1",
        "effective_source": SRC_AI,
    }
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
