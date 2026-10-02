"""Regression tests for lazy translation-cache reads."""

from __future__ import annotations

import json
from pathlib import Path

from translation_tool.core.lm_translator_shared_cache import fast_split_items_by_cache
from translation_tool.utils import cache_manager, cache_store


def _prepare_disk_cache(tmp_path: Path, monkeypatch):
    """Create one on-disk lang shard and reset the process runtime state."""
    cache_root = tmp_path / "cache"
    lang_dir = cache_root / "lang"
    lang_dir.mkdir(parents=True)
    (lang_dir / "lang_00001.json").write_text(
        json.dumps(
            {
                "item.example": {"src": "Hello", "dst": "哈囉"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cache_manager,
        "load_config",
        lambda: {
            "translator": {
                "cache_directory": "cache",
                "parallel_execution_workers": 1,
            }
        },
    )
    monkeypatch.setattr(
        cache_manager,
        "resolve_project_path",
        lambda path: tmp_path / path,
    )
    state = cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)
    state.initialized = False
    state.translation_cache = {}
    return state


def test_get_cache_dict_ref_lazily_initializes_existing_disk_cache(
    tmp_path, monkeypatch
):
    """First cache-dict read must expose an existing disk entry."""
    state = _prepare_disk_cache(tmp_path, monkeypatch)

    result = cache_manager.get_cache_dict_ref("lang")

    assert result["item.example"] == {"src": "Hello", "dst": "哈囉"}
    assert state.initialized is True


def test_get_from_cache_lazily_initializes_existing_cache(tmp_path, monkeypatch):
    """First value read must not return a false cache miss."""
    state = _prepare_disk_cache(tmp_path, monkeypatch)

    assert cache_manager.get_from_cache("lang", "item.example") == "哈囉"
    assert state.initialized is True


def test_get_cache_entry_lazily_initializes_existing_cache(tmp_path, monkeypatch):
    """First entry read must return the complete persisted entry."""
    state = _prepare_disk_cache(tmp_path, monkeypatch)

    assert cache_manager.get_cache_entry("lang", "item.example") == {
        "src": "Hello",
        "dst": "哈囉",
    }
    assert state.initialized is True


def test_shared_cache_split_keeps_existing_disk_entry_as_cache_hit(
    tmp_path, monkeypatch
):
    """Representative LM split flow must reuse a disk cache on its first read."""
    _prepare_disk_cache(tmp_path, monkeypatch)

    cached, pending = fast_split_items_by_cache(
        [
            {
                "path": "item.example",
                "source_text": "Hello",
                "text": "Hello",
                "cache_type": "lang",
            }
        ]
    )

    assert [item["text"] for item in cached] == ["哈囉"]
    assert pending == []


def test_is_cache_initialized_is_a_pure_state_query(tmp_path, monkeypatch):
    """The status query itself must not initialize or touch the cache root."""
    state = _prepare_disk_cache(tmp_path, monkeypatch)

    assert cache_manager.is_cache_initialized() is False
    assert state.initialized is False
    assert not (tmp_path / "cache" / "patchouli").exists()


def test_read_api_does_not_expose_partial_state_after_initialization_failure(
    tmp_path, monkeypatch
):
    """A failed multi-type load must not leak entries from earlier types."""
    state = _prepare_disk_cache(tmp_path, monkeypatch)
    real_loader = cache_manager._load_cache_type

    def fail_after_first(cache_type):
        if cache_type == "lang":
            return real_loader(cache_type)
        raise OSError("simulated cache type failure")

    monkeypatch.setattr(cache_manager, "_load_cache_type", fail_after_first)

    assert cache_manager.get_cache_dict_ref("lang") == {}
    assert state.initialized is False
    assert state.translation_cache == {}
