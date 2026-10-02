"""Regression tests for lazy translation-cache reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _fail_after_lang(monkeypatch):
    real_loader = cache_manager._load_cache_type

    def fail_after_first(cache_type):
        if cache_type == "lang":
            return real_loader(cache_type)
        raise OSError("simulated cache type failure")

    monkeypatch.setattr(cache_manager, "_load_cache_type", fail_after_first)


def _assert_no_pending_write(state):
    assert state.initialized is False
    assert state.translation_cache == {}
    assert not state.session_new_entries.get("lang")
    assert not state.is_dirty.get("lang")


def test_add_to_cache_does_not_accept_write_after_initialization_failure(
    tmp_path, monkeypatch
):
    """Failed initialization must not leave a non-durable pending write."""
    state = _prepare_disk_cache(tmp_path, monkeypatch)
    _fail_after_lang(monkeypatch)

    cache_manager.add_to_cache("lang", "new.key", "Hello", "哈囉")

    _assert_no_pending_write(state)


def test_add_to_cache_batch_does_not_accept_writes_after_initialization_failure(
    tmp_path, monkeypatch
):
    """Failed initialization must reject the whole batch."""
    state = _prepare_disk_cache(tmp_path, monkeypatch)
    _fail_after_lang(monkeypatch)

    cache_manager.add_to_cache_batch(
        "lang", [("a.key", "A", "甲"), ("b.key", "B", "乙")]
    )

    _assert_no_pending_write(state)


def test_add_to_cache_after_successful_initialization_creates_pending_entry(
    tmp_path, monkeypatch
):
    """Normal writes keep their pending/dirty behavior."""
    state = _prepare_disk_cache(tmp_path, monkeypatch)

    cache_manager.add_to_cache("lang", "new.key", "Hello", "哈囉")

    assert state.initialized is True
    assert state.session_new_entries["lang"]["new.key"]["dst"] == "哈囉"
    assert state.is_dirty["lang"] is True


def test_save_without_save_path_keeps_pending_entries(tmp_path, monkeypatch):
    """A missing save path must not silently drop pending entries."""
    state = _prepare_disk_cache(tmp_path, monkeypatch)
    cache_manager.add_to_cache("lang", "new.key", "Hello", "哈囉")
    state.cache_file_path = {}

    cache_manager.save_translation_cache("lang")

    assert "new.key" in state.session_new_entries["lang"]
    assert state.is_dirty["lang"] is True


def _count_failing_loader(monkeypatch):
    """Make every cache type load fail; return the call counter."""
    calls = []

    def always_fail(cache_type):
        calls.append(cache_type)
        raise OSError("simulated disk failure")

    monkeypatch.setattr(cache_manager, "_load_cache_type", always_fail)
    return calls


def test_write_apis_return_false_when_rejected(tmp_path, monkeypatch):
    """Rejected writes must be reported to callers."""
    _prepare_disk_cache(tmp_path, monkeypatch)
    _fail_after_lang(monkeypatch)

    assert cache_manager.add_to_cache("lang", "k", "A", "甲") is False
    assert cache_manager.add_to_cache_batch("lang", [("k", "A", "甲")]) is False


def test_write_apis_return_true_when_accepted_or_unchanged(tmp_path, monkeypatch):
    _prepare_disk_cache(tmp_path, monkeypatch)

    assert cache_manager.add_to_cache("lang", "k", "A", "甲") is True
    assert cache_manager.add_to_cache("lang", "k", "A", "甲") is True
    assert cache_manager.add_to_cache_batch("lang", [("k2", "B", "乙")]) is True
    assert cache_manager.add_to_cache("lang", "", "A", "甲") is False


def test_failed_initialization_is_not_retried_during_cooldown(tmp_path, monkeypatch):
    """Persistent failure must not re-run the full load on every access."""
    _prepare_disk_cache(tmp_path, monkeypatch)
    calls = _count_failing_loader(monkeypatch)
    now = [100.0]
    monkeypatch.setattr(cache_manager, "_monotonic", lambda: now[0])

    cache_manager.add_to_cache("lang", "a", "A", "甲")
    first_calls = len(calls)
    cache_manager.add_to_cache("lang", "b", "B", "乙")
    cache_manager.get_from_cache("lang", "a")

    assert first_calls == 1  # fails on the first cache type, stops there
    assert len(calls) == first_calls


def test_rejected_writes_log_once_per_failure_window(tmp_path, monkeypatch, caplog):
    _prepare_disk_cache(tmp_path, monkeypatch)
    _count_failing_loader(monkeypatch)

    with caplog.at_level("ERROR", logger=cache_manager.log.name):
        for i in range(5):
            cache_manager.add_to_cache("lang", f"k{i}", "A", "甲")

    rejected = [r for r in caplog.records if "拒絕" in r.getMessage()]
    assert len(rejected) == 1


def test_write_succeeds_after_cooldown_once_disk_recovers(tmp_path, monkeypatch):
    """After the cooldown, a recovered disk initializes and accepts writes."""
    state = _prepare_disk_cache(tmp_path, monkeypatch)
    real_loader = cache_manager._load_cache_type
    broken = [True]

    def flaky(cache_type):
        if broken[0]:
            raise OSError("simulated disk failure")
        return real_loader(cache_type)

    monkeypatch.setattr(cache_manager, "_load_cache_type", flaky)
    now = [100.0]
    monkeypatch.setattr(cache_manager, "_monotonic", lambda: now[0])

    assert cache_manager.add_to_cache("lang", "new.key", "Hello", "哈囉") is False
    _assert_no_pending_write(state)

    broken[0] = False
    now[0] += cache_manager._INIT_RETRY_COOLDOWN_SECONDS + 1

    assert cache_manager.add_to_cache("lang", "new.key", "Hello", "哈囉") is True
    assert state.initialized is True
    assert state.session_new_entries["lang"]["new.key"]["dst"] == "哈囉"
    assert state.is_dirty["lang"] is True


def test_reload_translation_cache_clears_partial_state_on_failure(
    tmp_path, monkeypatch
):
    state = _prepare_disk_cache(tmp_path, monkeypatch)
    real_loader = cache_manager.load_cache_type

    def fail_on_second(cache_type, **kwargs):
        if cache_type == "lang":
            return real_loader(cache_type, **kwargs)
        raise OSError("simulated reload failure")

    monkeypatch.setattr(cache_manager, "load_cache_type", fail_on_second)

    with pytest.raises(OSError):
        cache_manager.reload_translation_cache()

    assert state.initialized is False
    assert state.translation_cache == {}
    assert state.cache_file_path == {}


def test_reload_cache_type_does_not_load_into_uninitialized_state(
    tmp_path, monkeypatch
):
    state = _prepare_disk_cache(tmp_path, monkeypatch)
    calls = _count_failing_loader(monkeypatch)

    cache_manager.reload_translation_cache_type("lang")

    assert state.initialized is False
    assert state.translation_cache == {}
    assert calls  # explicit reload bypasses the cooldown and retries once


def test_cache_update_service_reports_rejected_write(tmp_path, monkeypatch):
    from app.services_impl.cache import cache_services

    _prepare_disk_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cache_manager, "get_cache_entry", lambda *_: {"src": "A", "dst": "甲"}
    )
    _count_failing_loader(monkeypatch)

    assert cache_services.cache_update_dst_service("lang", "k", "乙") is False
