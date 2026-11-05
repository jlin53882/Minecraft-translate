"""智慧校對的來源語系：payload、system prompt、批次分組、去重與修復路徑。"""

from __future__ import annotations

import json

import pytest

from translation_tool.core.source_locale import (
    SourceLocaleError,
    source_locale_supplement,
)
from translation_tool.translation_db import KIND_LANG, TranslationDB, WriteBackItem
from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.smart_review import run_smart_review
from translation_tool.translation_db.smart_review_filters import (
    _dedupe_same_text_rows,
)
from translation_tool.translation_db.smart_review_prompt import (
    SMART_REVIEW_REPAIR_SYSTEM_PROMPT,
    SMART_REVIEW_SYSTEM_PROMPT,
    build_smart_review_payload,
    build_smart_review_validation_repair_payload,
    smart_review_system_prompt,
)
from translation_tool.translation_db.smart_review_run import (
    _group_by_source_locale,
    _same_locale_run_length,
)


def _snap(ordinal: int, locale: str | None = None, text: str = "Hello"):
    return SmartReviewSnapshot(
        run_id="r",
        ordinal=ordinal,
        entry_id=ordinal,
        kind="lang",
        mc_version="1.20.1",
        mod_id="m",
        key=f"k{ordinal}",
        en_us=text,
        current_zh_tw="你好",
        effective_source=None,
        review_status=None,
        effective_revision=None,
        source_locale=locale,
    )


def test_default_payload_is_unchanged_golden():
    expected = {
        "items": [
            {
                "id": "1",
                "kind": "lang",
                "mc_version": "1.20.1",
                "mod_id": "m",
                "key": "k1",
                "source_text": "Hello",
                "current_translation": "你好",
                "context": [],
            }
        ]
    }
    for locale in (None, "en_us", "zh_tw", "zh_cn"):
        payload = build_smart_review_payload([_snap(1, locale)])
        assert payload == expected
        assert json.dumps(payload) == json.dumps(expected)


def test_non_default_payload_has_source_locale_key():
    item = build_smart_review_payload([_snap(1, "pt_br")])["items"][0]
    assert item["source_locale"] == "pt_br"
    assert (
        "source_locale" not in build_smart_review_payload([_snap(2, None)])["items"][0]
    )


@pytest.mark.parametrize("bad", ["", "Not A Locale!", "../x", "p" * 40, 5])
def test_invalid_locale_is_not_trusted(bad):
    snaps = [_snap(1, bad)]
    assert "source_locale" not in build_smart_review_payload(snaps)["items"][0]
    assert smart_review_system_prompt(SMART_REVIEW_SYSTEM_PROMPT, snaps) == (
        SMART_REVIEW_SYSTEM_PROMPT
    )


def test_default_system_prompt_equals_constants():
    for locale in (None, "en_us", "zh_tw", "zh_cn"):
        snaps = [_snap(1, locale)]
        assert smart_review_system_prompt(SMART_REVIEW_SYSTEM_PROMPT, snaps) is (
            SMART_REVIEW_SYSTEM_PROMPT
        )
        assert smart_review_system_prompt(SMART_REVIEW_REPAIR_SYSTEM_PROMPT, snaps) == (
            SMART_REVIEW_REPAIR_SYSTEM_PROMPT
        )


def test_non_default_system_prompt_appends_supplement():
    snaps = [_snap(1, "de_de"), _snap(2, "DE-de")]
    for base in (SMART_REVIEW_SYSTEM_PROMPT, SMART_REVIEW_REPAIR_SYSTEM_PROMPT):
        prompt = smart_review_system_prompt(base, snaps)
        assert prompt.startswith(base)
        assert prompt.endswith(source_locale_supplement("de_de"))
        assert "`de_de`" in prompt


def test_mixed_locale_request_fails_closed():
    with pytest.raises(SourceLocaleError):
        smart_review_system_prompt(
            SMART_REVIEW_SYSTEM_PROMPT, [_snap(1, "de_de"), _snap(2, "ja_jp")]
        )
    with pytest.raises(SourceLocaleError):
        smart_review_system_prompt(
            SMART_REVIEW_SYSTEM_PROMPT, [_snap(1, "de_de"), _snap(2, None)]
        )


def test_repair_payload_keeps_source_locale():
    snaps = [_snap(1, "pt_br"), _snap(2, "pt_br")]
    payload = build_smart_review_validation_repair_payload(
        snaps, [{"id": "2", "validation_error": "reason empty"}]
    )
    assert [item["id"] for item in payload["items"]] == ["2"]
    assert payload["items"][0]["source_locale"] == "pt_br"
    prompt = smart_review_system_prompt(SMART_REVIEW_REPAIR_SYSTEM_PROMPT, snaps[1:])
    assert "`pt_br`" in prompt


def test_runner_groups_batches_by_locale_stably():
    snaps = [
        _snap(1, "de_de"),
        _snap(2, None),
        _snap(3, "ja_jp"),
        _snap(4, "en_us"),
        _snap(5, "de_de"),
    ]
    grouped = list(_group_by_source_locale(snaps))
    assert [s.ordinal for s in grouped] == [2, 4, 1, 5, 3]
    assert _same_locale_run_length(grouped, 0) == 2
    assert _same_locale_run_length(grouped, 2) == 2
    assert _same_locale_run_length(grouped, 4) == 1
    defaults = [_snap(1), _snap(2, "en_us"), _snap(3)]
    assert list(_group_by_source_locale(defaults)) == defaults


def test_dedupe_does_not_merge_different_locales():
    def row(entry_id: int):
        return (
            entry_id,
            "lang",
            "1.20.1",
            "m",
            f"k{entry_id}",
            "Hello",
            "你好",
            1,
            "unreviewed",
        )

    rows = [row(1), row(2), row(3), row(4), row(5)]
    locales = {1: "pt_br", 2: "de_de", 3: "pt_br", 4: None, 5: "en_us"}
    kept = _dedupe_same_text_rows(rows, locales)
    assert [(r[0], twins) for r, twins in kept] == [
        (1, (3,)),
        (2, ()),
        (4, ()),
        (5, ()),
    ]
    # 沒有語系資訊時，每個條目都是獨立的 unknown，不能互相合併。
    assert [(r[0], t) for r, t in _dedupe_same_text_rows(rows)] == [
        (entry_id, ()) for entry_id in range(1, 6)
    ]


def _origin(db, entry_id: int, selector: str) -> None:
    locale = selector.split(":", 2)[1]
    db._conn.execute(
        "INSERT INTO entry_origin "
        "(entry_id, source_kind, source_path, selector, source_locale) "
        "VALUES (?, 'kubejs', 'kubejs/assets/m/lang/x.json', ?, ?)",
        (entry_id, selector, locale),
    )
    db._conn.commit()


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "locale.db")
    database.write_back(
        "1.20.1",
        [
            WriteBackItem(KIND_LANG, "m", "a_pt", "Olá", "你好"),
            WriteBackItem(KIND_LANG, "m", "b_pt", "Olá", "你好"),
            WriteBackItem(KIND_LANG, "m", "c_de", "Olá", "你好"),
            WriteBackItem(KIND_LANG, "m", "d_plain", "Olá", "你好"),
            WriteBackItem(KIND_LANG, "m", "e_plain", "Olá", "你好"),
        ],
        fill_other_versions=False,
    )
    ids = {
        key: database._one("SELECT id FROM entry WHERE key=?", (key,))[0]
        for key in ("a_pt", "b_pt", "c_de")
    }
    _origin(database, ids["a_pt"], "lang:pt_br:a_pt")
    _origin(database, ids["b_pt"], "lang:pt_br:b_pt")
    _origin(database, ids["c_de"], "lang:de_de:c_de")
    yield database
    database.close()


def test_candidates_carry_locale_and_split_twins(db):
    snaps = db.smart_review_candidates(limit=0)
    by_key = {s.key: s for s in snaps}
    assert by_key["a_pt"].source_locale == "pt_br"
    assert by_key["c_de"].source_locale == "de_de"
    assert by_key["d_plain"].source_locale is None
    assert by_key["e_plain"].source_locale is None
    assert len(snaps) == 4  # a_pt+b_pt 合併；c_de、d_plain、e_plain 各自獨立
    assert len(by_key["a_pt"].twin_entry_ids) == 1
    assert not by_key["d_plain"].twin_entry_ids
    assert not by_key["e_plain"].twin_entry_ids
    assert db.count_smart_review_candidates() == len(snaps)


def test_sql_and_python_dedupe_keep_known_default_locales_distinct(db):
    """SQL count 與 Python twin 補查都不能合併不同的已知預設語系。"""
    db.write_back(
        "1.20.1",
        [
            WriteBackItem(KIND_LANG, "default-locale", "en", "Hello", "你好"),
            WriteBackItem(KIND_LANG, "default-locale", "cn", "Hello", "你好"),
        ],
        fill_other_versions=False,
    )
    ids = {
        key: db._one(
            "SELECT id FROM entry WHERE mod_id=? AND key=?",
            ("default-locale", key),
        )[0]
        for key in ("en", "cn")
    }
    _origin(db, ids["en"], "lang:en_us:en")
    _origin(db, ids["cn"], "lang:zh_cn:cn")

    candidates = db.smart_review_candidates(
        version="1.20.1", mod_id="default-locale", limit=0
    )

    assert len(candidates) == 2
    assert (
        db.count_smart_review_candidates(version="1.20.1", mod_id="default-locale") == 2
    )
    assert all(not candidate.twin_entry_ids for candidate in candidates)


def test_run_sends_homogeneous_requests_with_supplement(db, monkeypatch):
    calls = []
    expected_ids = {
        str(snapshot.ordinal) for snapshot in db.smart_review_candidates(limit=50)
    }

    def fake_call(**kwargs):
        items = kwargs["payload"]["items"]
        calls.append((kwargs["system_prompt"], items))
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
                        "reason": "原文 Olá 與目前譯文 你好 語意一致，可以保留。",
                    }
                    for item in items
                ]
            }
        )

    monkeypatch.setattr(
        "translation_tool.translation_db.smart_review_provider.call_gemini_requests",
        fake_call,
    )
    run_smart_review(db, model_name="gemini-test", api_key="k", limit=50)
    assert len(calls) == 3
    sent_ids = {str(item["id"]) for _, items in calls for item in items}
    assert sent_ids == expected_ids
    for prompt, items in calls:
        locales = {item.get("source_locale") for item in items}
        assert len(locales) == 1
        locale = locales.pop()
        if locale is None:
            assert prompt == SMART_REVIEW_SYSTEM_PROMPT
        else:
            assert prompt == SMART_REVIEW_SYSTEM_PROMPT + "\n\n" + (
                source_locale_supplement(locale)
            )
