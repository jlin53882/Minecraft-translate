import flet as ft
import pytest

from app import view_registry as vr
from app.view_registry import DEFAULT_WINDOW_SIZE, get_window_size


def test_window_size_is_shared_by_all_views():
    assert get_window_size("cache") == DEFAULT_WINDOW_SIZE
    assert get_window_size("unknown") == DEFAULT_WINDOW_SIZE
    assert get_window_size() == DEFAULT_WINDOW_SIZE


# -- ViewSpec：單一資料來源 -------------------------------------------------


def test_spec_keys_are_unique_and_cover_all_views():
    keys = [spec.key for spec in vr.VIEW_SPECS]
    assert len(keys) == len(set(keys))
    assert set(keys) == {
        "dashboard",
        "pipeline",
        "extractor",
        "merge",
        "lm",
        "translation",
        "qc",
        "icon_preview",
        "cache",
        "rules",
        "lookup",
        "bundler",
        "config",
    }


def test_every_spec_belongs_to_a_known_group():
    groups = {g.key for g in vr.NAV_GROUPS} | {vr.SYSTEM_GROUP.key}
    assert all(spec.group in groups for spec in vr.VIEW_SPECS)
    # 設定固定在側欄底部
    assert vr.get_spec("config").group == vr.SYSTEM_GROUP.key
    assert [s.key for s in vr.specs_in_group("out")] == ["bundler"]


def test_nav_groups_are_ordered_by_workflow():
    assert [g.label for g in vr.NAV_GROUPS] == [
        "工作流程",
        "品管與校對",
        "資料庫",
        "輸出",
    ]
    # 工作台是工作流程的第一個，且是預設首頁；其後是一鍵流水線
    assert vr.specs_in_group("flow")[0].key == vr.DEFAULT_VIEW_KEY == "dashboard"
    assert vr.specs_in_group("flow")[1].key == "pipeline"


def test_shortcuts_are_unique_single_digits():
    shortcuts = [s.shortcut for s in vr.VIEW_SPECS if s.shortcut]
    assert len(shortcuts) == len(set(shortcuts))
    assert all(len(s) == 1 and s.isdigit() for s in shortcuts)


def test_icons_exist_and_labels_are_unique():
    labels = [s.label for s in vr.VIEW_SPECS]
    assert len(labels) == len(set(labels))
    valid_icons = {v.value for v in ft.Icons} if hasattr(ft.Icons, "__iter__") else None
    for spec in vr.VIEW_SPECS:
        assert spec.icon
        if valid_icons is not None:
            assert spec.icon in valid_icons


def test_view_modules_and_classes_are_importable():
    """延遲載入表的 module / class 名稱不能寫錯（ArnoLD 這類殘留 key 曾經沒人發現）。"""
    import importlib

    for spec in vr.VIEW_SPECS:
        module = importlib.import_module(spec.module)
        assert hasattr(module, spec.cls), (spec.key, spec.cls)


def test_get_spec_unknown_key():
    with pytest.raises(KeyError):
        vr.get_spec("nope")
    with pytest.raises(KeyError):
        vr.get_group("nope")


# -- registry ---------------------------------------------------------------


def test_registry_follows_spec_order_and_carries_group():
    registry = vr.build_view_registry(page=None, file_picker=None)
    assert [item["key"] for item in registry] == [s.key for s in vr.VIEW_SPECS]
    assert registry[0]["group"] == "flow"
    assert vr.index_of(registry, "config") == len(registry) - 1
    assert vr.index_of(registry, "nope") == -1


def test_views_are_built_lazily(monkeypatch):
    """B15：啟動時不建立全部頁面，第一次取用才建立，之後重用同一個實例。"""
    built = []

    def fake_import(key, page, file_picker):
        built.append(key)
        return object()

    monkeypatch.setattr(vr, "_lazy_import_view", fake_import)
    monkeypatch.setattr(vr, "wrap_view", lambda v: v)
    registry = vr.build_view_registry(page=None, file_picker=None)

    assert built == []
    assert registry[0]["key"] == "dashboard"
    assert vr.built_view(registry[2]) is None

    hooked = []
    registry[12].on_build(hooked.append)
    first = registry[12]["view"]
    assert built == ["config"]
    assert hooked == [first]
    assert registry[12]["view"] is first
    assert vr.built_view(registry[12]) is first
    assert built == ["config"]
