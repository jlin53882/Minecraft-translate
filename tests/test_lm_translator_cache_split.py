from __future__ import annotations

from translation_tool.core import lm_translator_shared_cache as shared_cache


def test_fast_split_items_by_cache_requires_src_match_for_lang(monkeypatch) -> None:
    monkeypatch.setattr(shared_cache, "value_fully_translated", lambda value: True)
    monkeypatch.setattr(
        shared_cache,
        "get_cache_dict_ref",
        lambda cache_type: {
            "item.demo.name": {"src": "Diamond", "dst": "鑽石"},
        },
    )

    cached_items, items_to_translate = shared_cache.fast_split_items_by_cache(
        [
            {
                "cache_type": "lang",
                "path": "item.demo.name",
                "source_text": "Diamond",
                "text": "Diamond",
            },
            {
                "cache_type": "lang",
                "path": "item.demo.name",
                "source_text": "Emerald",
                "text": "Emerald",
            },
        ]
    )

    assert len(cached_items) == 1
    assert cached_items[0]["text"] == "鑽石"
    assert len(items_to_translate) == 1
    assert items_to_translate[0]["source_text"] == "Emerald"


def test_fast_split_items_by_cache_uses_path_plus_source_for_patchouli(monkeypatch) -> None:
    monkeypatch.setattr(shared_cache, "value_fully_translated", lambda value: True)

    def fake_get_cache_dict_ref(cache_type: str):
        if cache_type == "patchouli":
            return {
                "entry.text|Hello": {"src": "ignored", "dst": "哈囉"},
            }
        return {}

    monkeypatch.setattr(shared_cache, "get_cache_dict_ref", fake_get_cache_dict_ref)

    cached_items, items_to_translate = shared_cache.fast_split_items_by_cache(
        [
            {
                "cache_type": "patchouli",
                "path": "entry.text",
                "source_text": "Hello",
                "text": "Hello",
            }
        ]
    )

    assert len(cached_items) == 1
    assert cached_items[0]["text"] == "哈囉"
    assert items_to_translate == []
