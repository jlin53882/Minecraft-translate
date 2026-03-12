from __future__ import annotations

from translation_tool.utils import cache_manager


def test_cache_manager_public_api_surface_exists() -> None:
    expected = [
        "reload_translation_cache",
        "reload_translation_cache_type",
        "save_translation_cache",
        "search_cache",
        "get_cache_entry",
        "get_cache_dict_ref",
        "get_cache_overview",
    ]

    for name in expected:
        assert hasattr(cache_manager, name), f"missing public API: {name}"


def test_get_cache_dict_ref_returns_live_reference_when_initialized() -> None:
    cache_manager._initialized = True
    cache_manager._translation_cache = {k: {} for k in cache_manager.CACHE_TYPES}
    cache_manager._translation_cache["lang"] = {"demo": {"src": "Hello", "dst": "哈囉"}}

    ref = cache_manager.get_cache_dict_ref("lang")
    ref["new-key"] = {"src": "World", "dst": "世界"}

    assert cache_manager._translation_cache["lang"]["new-key"] == {"src": "World", "dst": "世界"}


def test_cache_entry_and_dict_ref_are_safe_when_uninitialized() -> None:
    cache_manager._initialized = False
    cache_manager._translation_cache = {}

    assert cache_manager.get_cache_entry("lang", "missing") is None
    assert cache_manager.get_cache_dict_ref("lang") == {}
