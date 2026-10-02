"""翻譯快取惰性讀取的回歸測試。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.core.lm_translator_shared_cache import fast_split_items_by_cache
from translation_tool.utils import cache_manager, cache_store


def _prepare_disk_cache(tmp_path: Path, monkeypatch):
    """建立一個磁碟上的 lang 分片，並重置程序的 runtime 狀態。"""
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
    """第一次讀取快取字典時，必須能看到磁碟上既有的條目。"""
    state = _prepare_disk_cache(tmp_path, monkeypatch)

    result = cache_manager.get_cache_dict_ref("lang")

    assert result["item.example"] == {"src": "Hello", "dst": "哈囉"}
    assert state.initialized is True


def test_get_from_cache_lazily_initializes_existing_cache(tmp_path, monkeypatch):
    """第一次讀取值時，不可誤判為快取未命中。"""
    state = _prepare_disk_cache(tmp_path, monkeypatch)

    assert cache_manager.get_from_cache("lang", "item.example") == "哈囉"
    assert state.initialized is True


def test_get_cache_entry_lazily_initializes_existing_cache(tmp_path, monkeypatch):
    """第一次讀取條目時，必須回傳完整的已持久化條目。"""
    state = _prepare_disk_cache(tmp_path, monkeypatch)

    assert cache_manager.get_cache_entry("lang", "item.example") == {
        "src": "Hello",
        "dst": "哈囉",
    }
    assert state.initialized is True


def test_shared_cache_split_keeps_existing_disk_entry_as_cache_hit(
    tmp_path, monkeypatch
):
    """代表性的 LM 分流流程在第一次讀取時，必須沿用磁碟快取。"""
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
    """狀態查詢本身不可初始化，也不可碰觸快取根目錄。"""
    state = _prepare_disk_cache(tmp_path, monkeypatch)

    assert cache_manager.is_cache_initialized() is False
    assert state.initialized is False
    assert not (tmp_path / "cache" / "patchouli").exists()


def test_read_api_does_not_expose_partial_state_after_initialization_failure(
    tmp_path, monkeypatch
):
    """多類型載入失敗時，不可洩漏先前已載入類型的條目。"""
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
    """初始化失敗後，不可留下無法持久化的待寫入項目。"""
    state = _prepare_disk_cache(tmp_path, monkeypatch)
    _fail_after_lang(monkeypatch)

    cache_manager.add_to_cache("lang", "new.key", "Hello", "哈囉")

    _assert_no_pending_write(state)


def test_add_to_cache_batch_does_not_accept_writes_after_initialization_failure(
    tmp_path, monkeypatch
):
    """初始化失敗時，必須拒絕整批寫入。"""
    state = _prepare_disk_cache(tmp_path, monkeypatch)
    _fail_after_lang(monkeypatch)

    cache_manager.add_to_cache_batch(
        "lang", [("a.key", "A", "甲"), ("b.key", "B", "乙")]
    )

    _assert_no_pending_write(state)


def test_add_to_cache_after_successful_initialization_creates_pending_entry(
    tmp_path, monkeypatch
):
    """正常寫入仍須維持 pending / dirty 的行為。"""
    state = _prepare_disk_cache(tmp_path, monkeypatch)

    cache_manager.add_to_cache("lang", "new.key", "Hello", "哈囉")

    assert state.initialized is True
    assert state.session_new_entries["lang"]["new.key"]["dst"] == "哈囉"
    assert state.is_dirty["lang"] is True


def test_save_without_save_path_keeps_pending_entries(tmp_path, monkeypatch):
    """缺少儲存路徑時，不可靜默丟棄待寫入項目。"""
    state = _prepare_disk_cache(tmp_path, monkeypatch)
    cache_manager.add_to_cache("lang", "new.key", "Hello", "哈囉")
    state.cache_file_path = {}

    cache_manager.save_translation_cache("lang")

    assert "new.key" in state.session_new_entries["lang"]
    assert state.is_dirty["lang"] is True


def _count_failing_loader(monkeypatch):
    """讓每個快取類型的載入都失敗，並回傳呼叫次數記錄。"""
    calls = []

    def always_fail(cache_type):
        calls.append(cache_type)
        raise OSError("simulated disk failure")

    monkeypatch.setattr(cache_manager, "_load_cache_type", always_fail)
    return calls


def test_write_apis_return_false_when_rejected(tmp_path, monkeypatch):
    """被拒絕的寫入必須回報給呼叫端。"""
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
    """持續失敗時，不可在每次存取都重跑完整載入。"""
    _prepare_disk_cache(tmp_path, monkeypatch)
    calls = _count_failing_loader(monkeypatch)
    now = [100.0]
    monkeypatch.setattr(cache_manager, "_monotonic", lambda: now[0])

    cache_manager.add_to_cache("lang", "a", "A", "甲")
    first_calls = len(calls)
    cache_manager.add_to_cache("lang", "b", "B", "乙")
    cache_manager.get_from_cache("lang", "a")

    assert first_calls == 1  # 第一個快取類型就失敗，載入隨即中止
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
    """冷卻期過後，磁碟恢復時必須能初始化並接受寫入。"""
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
    assert calls  # 明確的重載會略過冷卻並重試一次


def test_cache_update_service_reports_rejected_write(tmp_path, monkeypatch):
    from app.services_impl.cache import cache_services

    _prepare_disk_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cache_manager, "get_cache_entry", lambda *_: {"src": "A", "dst": "甲"}
    )
    _count_failing_loader(monkeypatch)

    assert cache_services.cache_update_dst_service("lang", "k", "乙") is False


class _SpyFacade:
    def __init__(self):
        self.calls = []

    def rebuild_search_index(self, cache_types, translation_cache):
        self.calls.append(("all", dict(translation_cache)))

    def rebuild_search_index_for_type(self, cache_type, cache_types, translation_cache):
        self.calls.append((cache_type, dict(translation_cache)))


def _spy_facade(monkeypatch):
    spy = _SpyFacade()
    monkeypatch.setattr(cache_manager, "_get_search_facade", lambda: spy)
    return spy


def test_rebuild_search_index_does_not_run_after_initialization_failure(
    tmp_path, monkeypatch
):
    _prepare_disk_cache(tmp_path, monkeypatch)
    _count_failing_loader(monkeypatch)
    spy = _spy_facade(monkeypatch)

    assert cache_manager.rebuild_search_index() is False
    assert spy.calls == []


def test_rebuild_search_index_for_type_does_not_run_after_initialization_failure(
    tmp_path, monkeypatch
):
    _prepare_disk_cache(tmp_path, monkeypatch)
    _count_failing_loader(monkeypatch)
    spy = _spy_facade(monkeypatch)

    assert cache_manager.rebuild_search_index_for_type("lang") is False
    assert spy.calls == []


def test_rebuild_search_index_runs_when_cache_is_initialized(tmp_path, monkeypatch):
    _prepare_disk_cache(tmp_path, monkeypatch)
    spy = _spy_facade(monkeypatch)

    assert cache_manager.rebuild_search_index() is True
    assert cache_manager.rebuild_search_index_for_type("lang") is True

    assert [c[0] for c in spy.calls] == ["all", "lang"]
    assert spy.calls[0][1]["lang"]["item.example"]["dst"] == "哈囉"


def test_cache_rebuild_index_service_reports_failure_when_cache_init_fails(
    tmp_path, monkeypatch
):
    from app.services_impl.cache import cache_services

    _prepare_disk_cache(tmp_path, monkeypatch)
    _count_failing_loader(monkeypatch)
    spy = _spy_facade(monkeypatch)

    result = cache_services.cache_rebuild_index_service()

    assert result["success"] is False
    assert result["total_indexed"] == 0
    assert "✅" not in result["message"]
    assert result["error"]
    assert spy.calls == []


def test_cache_rebuild_index_service_reports_success_when_initialized(
    tmp_path, monkeypatch
):
    from app.services_impl.cache import cache_services

    _prepare_disk_cache(tmp_path, monkeypatch)
    _spy_facade(monkeypatch)

    result = cache_services.cache_rebuild_index_service()

    assert result["success"] is True
    assert result["total_indexed"] == 1


def test_reload_services_raise_when_index_rebuild_is_rejected(monkeypatch):
    from app.services_impl.cache import cache_services

    monkeypatch.setattr(cache_manager, "reload_translation_cache", lambda: None)
    monkeypatch.setattr(cache_manager, "reload_translation_cache_type", lambda t: None)
    monkeypatch.setattr(cache_manager, "rebuild_search_index", lambda: False)
    monkeypatch.setattr(cache_manager, "rebuild_search_index_for_type", lambda t: False)

    with pytest.raises(RuntimeError):
        cache_services.cache_reload_service()
    with pytest.raises(RuntimeError):
        cache_services.cache_reload_type_service("lang")
