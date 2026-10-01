"""ConfigStore：讀取、局部寫入（不固化預設值）、異動通知、主題偏好。"""

from __future__ import annotations

import json
import threading

import pytest

from app import config_store
from app.services_impl import config_service


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_service, "CONFIG_PATH", str(path))
    return path


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_get_reads_merged_config_with_defaults(cfg_path):
    assert config_store.get("translator.parallel_execution_workers") == 4
    assert config_store.get("no.such.path", "fallback") == "fallback"
    cfg_path.write_text(
        json.dumps({"translator": {"parallel_execution_workers": 9}}), encoding="utf-8"
    )
    assert config_store.get("translator.parallel_execution_workers") == 9
    # 使用者檔沒寫的欄位仍取得預設值
    assert config_store.get("translator.cache_directory")


def test_set_value_patches_only_that_field(cfg_path):
    cfg_path.write_text(
        json.dumps({"translator": {"cache_directory": "我的快取"}, "extra": 1}),
        encoding="utf-8",
    )
    assert config_store.set_value("ui.theme_mode", "light") is True
    saved = _read(cfg_path)
    assert saved == {
        "translator": {"cache_directory": "我的快取"},
        "extra": 1,
        "ui": {"theme_mode": "light"},
    }  # 其他欄位原封不動，預設值沒有被固化進檔案


def test_set_value_creates_missing_file_and_nested_path(cfg_path):
    assert not cfg_path.exists()
    assert config_store.set_value("a.b.c", 5) is True
    assert _read(cfg_path) == {"a": {"b": {"c": 5}}}


def test_set_value_replaces_non_dict_parent(cfg_path):
    cfg_path.write_text(json.dumps({"ui": "oops"}), encoding="utf-8")
    assert config_store.set_value("ui.theme_mode", "dark") is True
    assert _read(cfg_path)["ui"] == {"theme_mode": "dark"}


def test_set_value_never_overwrites_a_corrupt_file(cfg_path):
    cfg_path.write_text("{ not json", encoding="utf-8")
    assert config_store.set_value("ui.theme_mode", "light") is False
    assert cfg_path.read_text(encoding="utf-8") == "{ not json"


def test_set_value_rejects_non_object_root(cfg_path):
    cfg_path.write_text("[1, 2]", encoding="utf-8")
    assert config_store.set_value("x", 1) is False


def test_set_value_notifies_subscribers_once(cfg_path):
    calls: list[int] = []
    unsubscribe = config_store.subscribe(lambda: calls.append(1))
    try:
        config_store.set_value("x", 1)
        assert calls == [1]
    finally:
        unsubscribe()
    config_store.set_value("x", 2)
    assert calls == [1]  # 取消訂閱後不再通知


def test_failed_write_does_not_notify(cfg_path):
    cfg_path.write_text("{ not json", encoding="utf-8")
    calls: list[int] = []
    unsubscribe = config_store.subscribe(lambda: calls.append(1))
    try:
        config_store.set_value("x", 1)
    finally:
        unsubscribe()
    assert calls == []


def test_failing_subscriber_does_not_block_others(cfg_path):
    calls: list[int] = []

    def boom():
        raise RuntimeError("ui bug")

    unsub_a = config_store.subscribe(boom)
    unsub_b = config_store.subscribe(lambda: calls.append(1))
    try:
        assert config_store.set_value("x", 1) is True
        assert calls == [1]
    finally:
        unsub_a()
        unsub_b()


def test_legacy_save_path_notifies_too(cfg_path):
    """設定頁 / 合併頁走 config_service 存檔，也要通知外殼。"""
    calls: list[int] = []
    unsubscribe = config_store.subscribe(lambda: calls.append(1))
    try:
        config = config_store.snapshot()
        assert config_service.save_config_json(config) is True
    finally:
        unsubscribe()
    assert calls == [1]
    assert cfg_path.exists()


# -- 通知契約：callback 一律在寫入鎖釋放後執行 ------------------------------------


@pytest.fixture
def fresh_write_lock(monkeypatch):
    """每個測試用全新的寫入鎖：回歸時就算死鎖，也不會把鎖留給後面的測試。"""
    lock = threading.Lock()
    monkeypatch.setattr(config_store, "_write_lock", lock)
    return lock


def _run_with_timeout(fn, timeout=5.0):
    """在背景執行緒跑 fn；逾時代表卡住（死鎖），回傳 (是否完成, 例外)。"""
    box: dict = {}

    def target():
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - 測試要把例外帶回主執行緒
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    return (not thread.is_alive()), box


@pytest.mark.parametrize("how", ["save", "set_value", "legacy_service"])
def test_listeners_run_after_write_lock_is_released(cfg_path, fresh_write_lock, how):
    """不論從哪條路徑存檔，訂閱者被呼叫時 ``_write_lock`` 都必須已釋放。"""
    held_during_callback: list[bool] = []
    unsubscribe = config_store.subscribe(
        lambda: held_during_callback.append(fresh_write_lock.locked())
    )
    try:
        if how == "save":
            assert config_store.save(config_store.snapshot()) is True
        elif how == "set_value":
            assert config_store.set_value("x", 1) is True
        else:
            assert config_service.save_config_json(config_store.snapshot()) is True
    finally:
        unsubscribe()
    assert held_during_callback == [False]  # 只通知一次，且通知時沒有持鎖


def test_listener_can_write_config_without_deadlock(cfg_path, fresh_write_lock):
    """訂閱者 callback 再寫設定（巢狀寫入）不可死鎖，最終檔案仍是合法 JSON。"""
    calls: list[int] = []

    def listener():
        calls.append(1)
        if len(calls) == 1:  # 只在第一次通知時巢狀寫入，避免無窮遞迴
            assert config_store.set_value("ui.theme_mode", "light") is True

    unsubscribe = config_store.subscribe(listener)
    try:
        finished, box = _run_with_timeout(
            lambda: config_store.save(config_store.snapshot())
        )
    finally:
        unsubscribe()
    assert finished, "config_store.save 卡住：訂閱者的巢狀寫入造成死鎖"
    assert "error" not in box, box.get("error")
    assert box["result"] is True
    assert len(calls) == 2  # save 一次 + 巢狀 set_value 一次，沒有重複通知
    saved = _read(cfg_path)  # 檔案是合法 JSON，且巢狀寫入的值有留下
    assert saved["ui"]["theme_mode"] == "light"
    assert fresh_write_lock.locked() is False


def test_listener_can_write_config_from_set_value_without_deadlock(
    cfg_path, fresh_write_lock
):
    calls: list[int] = []

    def listener():
        calls.append(1)
        if len(calls) == 1:
            assert config_store.save(config_store.snapshot()) is True

    unsubscribe = config_store.subscribe(listener)
    try:
        finished, box = _run_with_timeout(lambda: config_store.set_value("x", 1))
    finally:
        unsubscribe()
    assert finished, "set_value 卡住：訂閱者的巢狀 save 造成死鎖"
    assert "error" not in box, box.get("error")
    assert len(calls) == 2
    assert _read(cfg_path)["x"] == 1


def test_save_notifies_exactly_once(cfg_path):
    calls: list[int] = []
    unsubscribe = config_store.subscribe(lambda: calls.append(1))
    try:
        assert config_store.save(config_store.snapshot()) is True
    finally:
        unsubscribe()
    assert calls == [1]


def test_failed_save_does_not_notify(cfg_path, monkeypatch):
    monkeypatch.setattr(
        "translation_tool.utils.config_manager.save_config", lambda *a, **k: False
    )
    calls: list[int] = []
    unsubscribe = config_store.subscribe(lambda: calls.append(1))
    try:
        assert config_store.save(config_store.snapshot()) is False
    finally:
        unsubscribe()
    assert calls == []


def test_failing_subscriber_does_not_block_others_on_save(cfg_path):
    calls: list[int] = []

    def boom():
        raise RuntimeError("ui bug")

    unsub_a = config_store.subscribe(boom)
    unsub_b = config_store.subscribe(lambda: calls.append(1))
    try:
        assert config_store.save(config_store.snapshot()) is True
        assert calls == [1]
    finally:
        unsub_a()
        unsub_b()


# -- 寫入所有權：所有 app 層的 config.json 寫入都經過同一把鎖 ---------------------------


@pytest.fixture
def lock_probe(cfg_path, fresh_write_lock, monkeypatch):
    """攔截真正寫檔的 ``save_config``，記錄每次寫入當下寫入鎖是否被持有，再交給真的實作。"""
    from translation_tool.utils import config_manager

    real_save = config_manager.save_config
    held: list[bool] = []

    def spy(config, config_path=None):
        held.append(fresh_write_lock.locked())
        return real_save(config, config_path)

    monkeypatch.setattr(config_manager, "save_config", spy)
    return held


@pytest.mark.parametrize("how", ["save", "set_value", "legacy_service"])
def test_every_config_write_path_holds_the_write_lock(cfg_path, lock_probe, how):
    if how == "save":
        assert config_store.save(config_store.snapshot()) is True
    elif how == "set_value":
        assert config_store.set_value("x", 1) is True
    else:
        assert config_service.save_config_json(config_store.snapshot()) is True

    assert lock_probe == [True]  # 唯一一次寫檔是在持鎖狀態下進行的


def test_merge_page_field_change_goes_through_the_config_store(cfg_path, lock_probe):
    """合併頁單欄位寫入不可繞過 ConfigStore（鎖、通知）。"""
    from types import SimpleNamespace

    from app.views.merge_view import MergeView

    broadcasts: list[int] = []
    stub = SimpleNamespace(
        _broadcast_config_change_to_config_view=lambda: broadcasts.append(1)
    )
    calls: list[int] = []
    unsubscribe = config_store.subscribe(lambda: calls.append(1))
    try:
        MergeView._on_merge_field_changed(stub, "pending_folder_name", "我的待翻譯")
    finally:
        unsubscribe()

    assert lock_probe == [True]
    assert calls == [1]  # 外殼等訂閱者也會收到通知
    assert broadcasts == [1]
    assert _read(cfg_path) == {"lang_merger": {"pending_folder_name": "我的待翻譯"}}


def test_concurrent_writers_never_leave_a_broken_file_or_deadlock(cfg_path):
    """兩個寫入者（ConfigStore 與舊的 service 路徑）同時寫：檔案永遠可解析、通知次數正確。"""
    calls: list[int] = []
    unsubscribe = config_store.subscribe(lambda: calls.append(1))
    rounds = 40
    blob = {"filler": ["x" * 50] * 200}  # 加大內容，提高交錯寫入時出事的機率
    errors: list[BaseException] = []

    def store_writer():
        try:
            for i in range(rounds):
                assert config_store.set_value("store.counter", i) is True
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def legacy_writer():
        try:
            for i in range(rounds):
                cfg = config_store.snapshot()
                cfg["legacy"] = {"counter": i, **blob}
                assert config_service.save_config_json(cfg) is True
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=store_writer),
        threading.Thread(target=legacy_writer),
    ]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        unsubscribe()

    assert not any(t.is_alive() for t in threads), "寫入卡住（死鎖）"
    assert not errors, errors
    assert isinstance(_read(cfg_path), dict)  # 永遠是合法 JSON
    assert len(calls) == rounds * 2  # 每次成功寫入恰好通知一次


def test_save_normalizes_dependent_flags(cfg_path):
    config = config_store.snapshot()
    config["lang_merger"]["process_zh_cn_files"] = False
    config["lang_merger"]["skip_zh_cn_when_only_process_lang"] = True
    assert config_store.save(config) is True
    assert _read(cfg_path)["lang_merger"]["skip_zh_cn_when_only_process_lang"] is False


def test_snapshot_is_an_independent_copy(cfg_path):
    first = config_store.snapshot()
    first["translator"]["parallel_execution_workers"] = 99
    assert config_store.snapshot()["translator"]["parallel_execution_workers"] != 99


# -- 主題偏好 -----------------------------------------------------------------


def test_theme_mode_defaults_to_dark_and_roundtrips(cfg_path):
    assert config_store.get_theme_mode() == "dark"
    assert config_store.set_theme_mode("light") is True
    assert config_store.get_theme_mode() == "light"
    assert config_store.set_theme_mode("dark") is True
    assert config_store.get_theme_mode() == "dark"


def test_invalid_theme_value_in_file_falls_back_to_dark(cfg_path):
    cfg_path.write_text(json.dumps({"ui": {"theme_mode": "purple"}}), encoding="utf-8")
    assert config_store.get_theme_mode() == "dark"


def test_set_theme_mode_rejects_unknown_mode(cfg_path):
    with pytest.raises(ValueError):
        config_store.set_theme_mode("purple")
    assert not cfg_path.exists()
