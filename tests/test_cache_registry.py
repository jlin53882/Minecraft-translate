"""cache_registry：登錄、清除、重複登錄與各模組快取的清除入口。"""

from __future__ import annotations

import pytest

import translation_tool.core.lang_merge_content_copy as content_copy
import translation_tool.translation_db.repository
import translation_tool.translation_db.settings as db_settings
import translation_tool.translation_db.smart_review
import translation_tool.translation_db.smart_review_history  # noqa: F401

# 先 import 所有登錄快取的模組：模組在 import 時登錄，若在隔離登錄表期間才首次 import 會遺失登錄。
from translation_tool.core import official_locale
from translation_tool.utils import (
    app_paths,
    cache_registry,
    text_matching,
    text_processor,
)


@pytest.fixture
def isolated_registry(monkeypatch):
    """用空登錄表隔離測試，結束後還原真實登錄。"""
    monkeypatch.setattr(cache_registry, "_registry", {})
    monkeypatch.setattr(cache_registry, "register_smart_review_caches", list)
    return cache_registry


def test_register_and_list_names(isolated_registry):
    isolated_registry.register_cache("a", lambda: None)
    isolated_registry.register_cache("b", lambda: None)
    assert isolated_registry.registered_cache_names() == ["a", "b"]


def test_clear_all_caches_calls_every_clear(isolated_registry):
    calls: list[str] = []
    isolated_registry.register_cache("a", lambda: calls.append("a"))
    isolated_registry.register_cache("b", lambda: calls.append("b"))
    assert isolated_registry.clear_all_caches() == []
    assert calls == ["a", "b"]


def test_duplicate_registration_replaces_and_calls_once(isolated_registry):
    calls: list[str] = []
    isolated_registry.register_cache("a", lambda: calls.append("old"))
    isolated_registry.register_cache("a", lambda: calls.append("new"))
    assert isolated_registry.registered_cache_names() == ["a"]
    isolated_registry.clear_all_caches()
    assert calls == ["new"]


def test_failed_clear_does_not_block_others(isolated_registry):
    calls: list[str] = []

    def boom() -> None:
        raise RuntimeError("x")

    isolated_registry.register_cache("bad", boom)
    isolated_registry.register_cache("ok", lambda: calls.append("ok"))
    assert isolated_registry.clear_all_caches() == ["bad"]
    assert calls == ["ok"]


def test_register_rejects_invalid_arguments(isolated_registry):
    with pytest.raises(ValueError):
        isolated_registry.register_cache("", lambda: None)
    with pytest.raises(TypeError):
        isolated_registry.register_cache("x", None)  # type: ignore[arg-type]


def test_smart_review_registration_is_lazy_and_tolerates_missing(monkeypatch):
    monkeypatch.setattr(cache_registry, "_registry", {})
    names = cache_registry.register_smart_review_caches()
    assert set(names) == set(cache_registry.registered_cache_names())


def test_module_caches_are_registered_and_clearable():
    names = set(cache_registry.registered_cache_names())
    assert {
        "translation_db.settings.registry",
        "core.official_locale.index",
        "core.lang_merge_content_copy.patchouli_eff",
        "utils.text_processor.conflict_warnings",
        "utils.text_processor.replace_rules",
        "utils.app_paths.migrated_roots",
        "translation_db.repository.quality_comparison",
        "utils.text_matching.normalize_term",
        "utils.config_manager.config",
    } <= names

    db_settings._registry_cache["x"] = ((), {})  # type: ignore[index]
    app_paths._migrated_roots.add("x")  # type: ignore[arg-type]
    text_processor._WARNED_CONFLICT_SIGNATURES.add(("x",))
    content_copy._patchouli_eff_cache[("k",)] = {"zh_cn": True}
    text_matching.normalize_term_text("Foo_Bar", casefold=True)
    assert text_matching.term_normalize_cache_info().currsize >= 1

    assert cache_registry.clear_all_caches() == []

    assert not db_settings._registry_cache
    assert not app_paths._migrated_roots
    assert not text_processor._WARNED_CONFLICT_SIGNATURES
    assert not content_copy._patchouli_eff_cache
    assert text_matching.term_normalize_cache_info().currsize == 0
    assert official_locale.load_official_locale_index.cache_info().currsize == 0


def test_patchouli_clear_keeps_inflight_entries():
    flight = content_copy._PatchouliSingleFlight()
    content_copy._patchouli_eff_cache[("inflight",)] = flight
    content_copy._patchouli_eff_cache[("done",)] = {"zh_cn": True}
    try:
        content_copy.clear_patchouli_eff_cache()
        assert ("inflight",) in content_copy._patchouli_eff_cache
        assert ("done",) not in content_copy._patchouli_eff_cache
    finally:
        content_copy._patchouli_eff_cache.pop(("inflight",), None)
