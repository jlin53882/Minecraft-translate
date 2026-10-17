"""官方 Minecraft 語系、LLM 術語提示與 Mod DB batch 邊界契約。"""

from __future__ import annotations

import json

from app.services_impl.moddb_translate_service import (
    _attach_translation_sessions,
    build_items,
)
from translation_tool.core.lm_format_policy import (
    FORMAT_POLICY_PROMPT,
    LANG_SEMANTIC_FORMAT_POLICY_PROMPT,
    PATCHOULI_SEMANTIC_FORMAT_POLICY_PROMPT,
    STRUCTURED_FORMAT_POLICY_PROMPT,
    normalize_legacy_prompt,
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


def test_legacy_prompt_normalization_preserves_custom_rules() -> None:
    legacy_breaks = (
        "13. 保留每筆 value 原有的實際換行數量；可依譯文語意調整換行位置，但不可新增或刪除實際換行\n"
        "14. 字面上的反斜線+n（\\n）必須原樣保留，不可轉成實際換行；實際換行也不可改成字面上的反斜線+n。"
    )
    custom = "專有名詞 Cobblestone 一律翻譯為鵝卵石。"

    normalized = normalize_legacy_prompt(f"自訂翻譯規則\n{legacy_breaks}\n{custom}\n")

    assert custom in normalized
    assert "保留每筆 value 原有的實際換行數量" not in normalized
    assert "字面上的反斜線+n" not in normalized

    effective = build_translation_prompt(normalized, [], profile="lang")
    assert FORMAT_POLICY_PROMPT in effective
    assert "以下格式契約優先於前文任何互相衝突的換行或格式規則。" in effective


def test_existing_prompt_normalization_is_exact_line_scoped() -> None:
    custom_variant = "我的規則：保留每筆 value 原有的實際換行數量，但允許語意重排。"

    assert normalize_legacy_prompt(custom_variant) == custom_variant


def test_patchouli_structure_policy_is_kind_scoped() -> None:
    prompt = build_translation_prompt(
        "基礎",
        [{"text": "Book", "source_text": "Book", "cache_type": "patchouli"}],
        profile="patch",
    )

    assert FORMAT_POLICY_PROMPT in prompt
    assert STRUCTURED_FORMAT_POLICY_PROMPT in prompt
    assert PATCHOULI_SEMANTIC_FORMAT_POLICY_PROMPT in prompt

    lang_prompt = build_translation_prompt(
        "基礎",
        [{"text": "Hello", "source_text": "Hello", "cache_type": "lang"}],
        profile="lang",
    )
    assert STRUCTURED_FORMAT_POLICY_PROMPT not in lang_prompt
    assert LANG_SEMANTIC_FORMAT_POLICY_PROMPT in lang_prompt
    assert PATCHOULI_SEMANTIC_FORMAT_POLICY_PROMPT not in lang_prompt


def test_semantic_newline_policies_are_precise_and_kind_scoped() -> None:
    patchouli_prompt = build_translation_prompt(
        "基礎",
        [{"text": "了解 this", "source_text": "了解 this", "cache_type": "patchouli"}],
        profile="patch",
    )
    lang_prompt = build_translation_prompt(
        "基礎",
        [{"text": "了解 this", "source_text": "了解 this", "cache_type": "lang"}],
        profile="lang",
    )

    assert "了解\\n這個" in patchouli_prompt
    assert "護甲和\\n武器" in patchouli_prompt
    assert "完整句子、完整段落" in patchouli_prompt
    assert "了解\\n這個" in lang_prompt
    assert "護甲和\\n武器" in lang_prompt
    assert PATCHOULI_SEMANTIC_FORMAT_POLICY_PROMPT not in lang_prompt


def test_missing_prompt_uses_new_builtin_prompt() -> None:
    from translation_tool.core import lm_translator_main as main

    context = main._build_batch_prompt_context(
        [
            {
                "path": "key",
                "source_text": "Hello",
                "text": "Hello",
                "cache_type": "lang",
            }
        ],
        {},
    )

    assert "你正在翻譯 Minecraft 語言檔案" in context.effective_prompt
    assert "以下格式契約優先於前文任何互相衝突的換行或格式規則。" in (
        context.effective_prompt
    )


def test_gemini_and_openai_share_the_same_effective_prompt() -> None:
    from translation_tool.core import lm_translator_main as main

    items = [
        {
            "path": "key",
            "source_text": "Hello",
            "text": "Hello",
            "cache_type": "lang",
        }
    ]
    base = {
        "lang_system_prompt": "自訂 prompt\n保留專有名詞 Cobblestone",
        "patchouli_system_prompt": "Patchouli prompt",
    }

    gemini = main._build_batch_prompt_context(items, {**base, "provider": "gemini"})
    openai = main._build_batch_prompt_context(items, {**base, "provider": "chatgpt"})

    assert gemini.effective_prompt == openai.effective_prompt
    assert "保留專有名詞 Cobblestone" in gemini.effective_prompt


def test_moddb_reviewed_lang_glossary_is_injected_for_patchouli_routes() -> None:
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
    for profile in ("patch", "md"):
        assert "Copper Gate → 銅製閘門" in build_translation_prompt(
            "基礎", [{**item}], profile=profile
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


def test_moddb_sessions_are_shared_by_module_and_isolated_between_modules() -> None:
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
    assert items[0]["_translation_session"] is items[1]["_translation_session"]
    assert items[0]["_translation_session"] is not items[3]["_translation_session"]
    assert items[0]["_translation_session"].key.endswith(":mod-a")
    assert items[1]["_translation_session"].key.endswith(":mod-a")
    assert glossary_calls == ["mod-a", "mod-b"]


def test_moddb_context_skips_reviewed_glossary_when_disabled(monkeypatch) -> None:
    from translation_tool.core import lm_moddb_context

    monkeypatch.setattr(
        lm_moddb_context,
        "load_config",
        lambda: {"lm_translator": {"moddb_reviewed_glossary": False}},
    )
    items = build_items([(1, KIND_LANG, "mod-a", "lang.key", "Language")])
    calls: list[str] = []

    _attach_translation_sessions(
        items,
        operation_id="disabled-glossary",
        db=type(
            "FakeDB",
            (),
            {
                "reviewed_lang_glossary": lambda _self, mod_id: calls.append(mod_id),
            },
        )(),
    )

    assert calls == []
    assert "_reviewed_glossary" not in items[0]


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
