"""ConfigStore：讀取、局部寫入（不固化預設值）、異動通知、主題偏好。"""

from __future__ import annotations

import json

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
