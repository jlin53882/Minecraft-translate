"""官方 Minecraft 語系、LLM 術語提示與 Mod DB batch 邊界契約。"""

from __future__ import annotations

import json

from app.services_impl.moddb_translate_service import (
    _attach_translation_sessions,
    build_items,
)
from translation_tool.core.lm_session import TranslationSessionState
from translation_tool.core.lm_translator_shared_loop import (
    translate_items_with_cache_loop,
)
from translation_tool.core.official_locale import (
    OfficialLocaleIndex,
    OfficialLocaleRecord,
    build_translation_prompt,
    load_official_locale_index,
)
from translation_tool.translation_db.schema import (
    KIND_ADVANCEMENT,
    KIND_GUIDE,
    KIND_LANG,
    KIND_PATCHOULI,
)


def test_bundled_official_locale_is_valid_and_key_aware() -> None:
    index = load_official_locale_index()

    assert index.available
    assert len(index.records_by_key) >= 8_500
    assert (
        index.lookup_exact("block.minecraft.cobblestone", "Cobblestone", "圓石") is None
    )
    assert (
        index.lookup_exact("block.minecraft.cobblestone", "Cobblestone", "圆石")
        == "鵝卵石"
    )


def test_ambiguous_text_fallback_is_not_used() -> None:
    index = OfficialLocaleIndex(
        records_by_key={
            "one": OfficialLocaleRecord("one", "Same", "相同", "甲"),
            "two": OfficialLocaleRecord("two", "Same", "相同", "乙"),
        },
        unique_by_text_pair={},
        unique_tw_by_en={},
        revision="test",
    )

    assert index.lookup_unique_text("Same", "相同") is None


def test_official_terms_are_injected_for_every_translation_profile() -> None:
    items = [
        {
            "path": "block.minecraft.cobblestone",
            "source_text": "Cobblestone",
            "text": "Cobblestone",
        }
    ]

    for profile in ("lang", "patch", "md"):
        prompt = build_translation_prompt("基礎提示詞", items, profile=profile)
        assert "官方 Minecraft 術語" in prompt
        assert "Cobblestone → 鵝卵石" in prompt


def test_moddb_reviewed_glossary_is_lang_only() -> None:
    item = {
        "path": "item.example",
        "source_text": "Example",
        "text": "Example",
        "_reviewed_glossary": [
            {"en_us": "Copper Gate", "zh_tw": "銅製閘門"},
        ],
    }

    assert "Copper Gate → 銅製閘門" in build_translation_prompt(
        "基礎", [item], profile="lang"
    )
    assert "Copper Gate → 銅製閘門" not in build_translation_prompt(
        "基礎", [{**item}], profile="patch"
    )


def test_moddb_items_form_module_and_kind_batch_boundaries() -> None:
    items = build_items(
        [
            (1, KIND_LANG, "mod-a", "a", "A"),
            (2, KIND_LANG, "mod-a", "b", "B"),
            (3, KIND_GUIDE, "mod-a", "guide.md#0", "Guide"),
            (4, KIND_PATCHOULI, "mod-b", "book#0", "Book"),
            (5, KIND_ADVANCEMENT, "mod-b", "adv#0", "Adv"),
        ]
    )
    calls: list[list[dict]] = []

    def fake_translate(batch, _total):
        calls.append(list(batch))
        return [{**item, "text": f"翻:{item['text']}"} for item in batch], "DONE"

    result = translate_items_with_cache_loop(
        items,
        total_for_smart=len(items),
        translate_batch_smart=fake_translate,
        write_new_cache=False,
        reload_cache=False,
        cache_add=lambda *_args, **_kwargs: True,
        cache_save=lambda *_args, **_kwargs: True,
    )

    assert result.status == "DONE"
    assert [
        {(item["_mod_id"], item["_kind"]) for item in batch} for batch in calls
    ] == [
        {("mod-a", KIND_LANG)},
        {("mod-a", KIND_GUIDE)},
        {("mod-b", KIND_PATCHOULI)},
        {("mod-b", KIND_ADVANCEMENT)},
    ]


def test_moddb_sessions_are_isolated_by_module_and_kind() -> None:
    items = build_items(
        [
            (1, KIND_LANG, "mod-a", "lang.key", "Language"),
            (2, KIND_GUIDE, "mod-a", "guide.md#0", "Guide"),
            (3, KIND_LANG, "mod-a", "lang.other", "Other"),
            (4, KIND_LANG, "mod-b", "lang.key", "Language"),
        ]
    )
    glossary_calls: list[str] = []

    class FakeDB:
        def reviewed_lang_glossary(self, mod_id: str) -> list[dict[str, str]]:
            glossary_calls.append(mod_id)
            return []

    _attach_translation_sessions(items, operation_id="op-1", db=FakeDB())

    assert items[0]["_translation_session"] is items[2]["_translation_session"]
    assert items[0]["_translation_session"] is not items[1]["_translation_session"]
    assert items[0]["_translation_session"] is not items[3]["_translation_session"]
    assert items[0]["_translation_session"].key.endswith(":mod-a:lang")
    assert items[1]["_translation_session"].key.endswith(":mod-a:guide")
    assert glossary_calls == ["mod-a", "mod-b"]


def test_official_glossary_keeps_key_specific_duplicate_english_values(
    monkeypatch,
) -> None:
    from translation_tool.core import official_locale

    index = official_locale.OfficialLocaleIndex(
        records_by_key={
            "key.a": official_locale.OfficialLocaleRecord(
                "key.a", "Charge", "充能", "充電"
            ),
            "key.b": official_locale.OfficialLocaleRecord(
                "key.b", "Charge", "充能", "收費"
            ),
        },
        unique_by_text_pair={},
        unique_tw_by_en={},
        revision="test",
    )
    monkeypatch.setattr(official_locale, "load_official_locale_index", lambda: index)

    items = [
        {"path": "key.a", "source_text": "Charge"},
        {"path": "key.b", "source_text": "Charge"},
    ]
    assert index.glossary_for_items(items) == [
        ("key.a", "Charge", "充電"),
        ("key.b", "Charge", "收費"),
    ]
    prompt = official_locale.build_translation_prompt("基礎", items, profile="lang")
    assert "key「key.a」：Charge → 充電" in prompt
    assert "key「key.b」：Charge → 收費" in prompt


def test_official_exact_items_can_finish_without_api_credentials(monkeypatch) -> None:
    from translation_tool.core import lm_translator_main as main

    monkeypatch.setattr(
        main,
        "_execute_translation",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不應呼叫 API 流程")),
    )
    item = {
        "path": "block.minecraft.cobblestone",
        "source_text": "Cobblestone",
        "text": "Cobblestone",
        "zh_cn": "圆石",
        "cache_type": "lang",
    }

    result, status = main.translate_batch_smart([item], 1)

    assert status == "AUTO"
    assert result[0]["text"] == "鵝卵石"
    assert result[0]["_official_exact"] is True


def test_translation_session_rolls_history_without_changing_session_key() -> None:
    state = TranslationSessionState("moddb:operation-1:mod-a", max_history_tokens=20)
    state.replace(
        [
            {"role": "user", "text": "第一批"},
            {"role": "model", "text": "第一批譯文"},
            {"role": "user", "text": "第二批"},
            {"role": "model", "text": "第二批譯文"},
        ]
    )

    assert state.key == "moddb:operation-1:mod-a"
    assert state.history[-1]["text"] == "第二批譯文"
    assert all(turn["role"] in {"user", "model"} for turn in state.history)


def test_translation_runtime_uses_trimmed_session_history_between_internal_batches(
    monkeypatch,
) -> None:
    from translation_tool.core import lm_translator_main as main

    config = {
        "lm_translator": {
            "models": {"m": {"enabled": True}},
            "temperature": 0.2,
            "retry_same_as_source": False,
            "token_budget_enabled": False,
            "max_output_tokens": 2048,
            "initial_batch_size_lang": 1,
            "initial_batch_size_patchouli": 1,
            "initial_batch_size_md": 1,
            "initial_batch_size_ftb": 1,
            "initial_batch_size_kubejs": 1,
            "lang_system_prompt": "P",
            "patchouli_system_prompt": "P",
        }
    }
    monkeypatch.setattr(main, "load_config", lambda: config)
    monkeypatch.setattr(main, "get_translation_provider", lambda: "gemini")
    monkeypatch.setattr(main, "get_api_key_count", lambda: 1)
    monkeypatch.setattr(main, "interruptible_sleep", lambda _seconds: None)
    monkeypatch.setattr(main, "raise_if_cancelled", lambda: None)
    state = TranslationSessionState("moddb:operation-1:mod-a", max_history_tokens=1)
    calls = []

    def fake_api(**kwargs):
        calls.append(kwargs)
        return json.dumps(
            {
                "items": [
                    {"id": item["id"], "value": "T"}
                    for item in kwargs["payload"]["items"]
                ]
            }
        )

    monkeypatch.setattr(main, "call_gemini_requests", fake_api)
    items = [
        {
            "path": str(index),
            "text": f"source{index}",
            "source_text": f"source{index}",
            "cache_type": "lang",
            "_translation_session": state,
        }
        for index in range(3)
    ]

    result, status = main.translate_batch_smart(items, 3)

    assert status == "AUTO"
    assert len(result) == 3
    assert [len(call.get("conversation_history", [])) for call in calls] == [0, 2, 2]
    assert len(state.history) == 2
