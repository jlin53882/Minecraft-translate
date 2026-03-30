"""tests/test_icon_preview_jar_entries.py

測試 icon_preview_view 的 JAR 目錄讀取功能。
驗證 _load_entries_from_jar_directory() 的各種情境。
"""

import pytest
import zipfile
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import flet as ft


class MockPage:
    def __init__(self):
        self.overlay = []


def create_test_jar(jar_dir: Path, jar_name: str, files: dict[str, str]) -> Path:
    """在 jar_dir 建立一個測試用 JAR 檔案"""
    jar_path = jar_dir / jar_name
    with zipfile.ZipFile(jar_path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return jar_path


def create_view(
    source_root: Path | None = None,
    review_root: Path | None = None,
):
    """建立 IconPreviewView 並設定 source_root 和 review_root"""
    from app.views.icon_preview_view import IconPreviewView

    with patch.object(IconPreviewView, "__init__", lambda self, page: None):
        view = IconPreviewView.__new__(IconPreviewView)
        view.page = MockPage()
        view.source_root = source_root
        view.review_root = review_root
        # 快取初始狀態
        view._entries_cache = None
        view._cache_meta = {}
    return view


class TestLoadEntriesFromJarDirectory:
    """_load_entries_from_jar_directory 的各種情境測試"""

    def test_basic_jar_read(self, tmp_path):
        """基本 JAR 讀取：單一 JAR，含 en_us.json"""
        jar_dir = tmp_path / "mods"
        jar_dir.mkdir()

        create_test_jar(jar_dir, "actuallyadditions-1.20.jar", {
            "assets/actuallyadditions/lang/en_us.json": json.dumps({
                "item.actuallyadditions.atomic_reconstructor": "Atomic Reshaper",
                "item.actuallyadditions.manual": "Manual",
            }),
        })

        view = create_view(source_root=jar_dir, review_root=None)
        entries = view._load_entries_from_jar_directory()

        assert len(entries) == 2
        modids = {e.modid for e in entries}
        assert "actuallyadditions" in modids

    def test_modid_parsed_correctly(self, tmp_path):
        """modid 應該從路徑正確解析（assets/<modid>/lang/en_us.json）"""
        jar_dir = tmp_path / "mods"
        jar_dir.mkdir()

        create_test_jar(jar_dir, "jei-1.20.1.jar", {
            "assets/jei/lang/en_us.json": json.dumps({
                "jei.category.brewing": "Brewing",
            }),
        })

        view = create_view(source_root=jar_dir, review_root=None)
        entries = view._load_entries_from_jar_directory()

        assert len(entries) == 1
        assert entries[0].modid == "jei"

    def test_multiple_jars(self, tmp_path):
        """多個 JAR 的情況"""
        jar_dir = tmp_path / "mods"
        jar_dir.mkdir()

        create_test_jar(jar_dir, "mod1.jar", {
            "assets/mod1/lang/en_us.json": json.dumps({"mod1.key": "value1"}),
        })
        create_test_jar(jar_dir, "mod2.jar", {
            "assets/mod2/lang/en_us.json": json.dumps({"mod2.key": "value2"}),
        })

        view = create_view(source_root=jar_dir, review_root=None)
        entries = view._load_entries_from_jar_directory()

        assert len(entries) == 2
        modids = {e.modid for e in entries}
        assert modids == {"mod1", "mod2"}

    def test_bad_zip_ignored(self, tmp_path):
        """zipfile.BadZipFile 應該被忽略，不影響其他 JAR"""
        jar_dir = tmp_path / "mods"
        jar_dir.mkdir()

        # 建立壞掉的 JAR
        bad_jar = jar_dir / "bad.jar"
        bad_jar.write_bytes(b"not a zip file at all")

        # 建立好的 JAR
        create_test_jar(jar_dir, "good.jar", {
            "assets/good/lang/en_us.json": json.dumps({"good.key": "good value"}),
        })

        view = create_view(source_root=jar_dir, review_root=None)
        entries = view._load_entries_from_jar_directory()

        # 好 JAR 應該正常處理
        assert len(entries) == 1
        assert entries[0].modid == "good"

    def test_no_jars_returns_empty(self, tmp_path):
        """沒有 JAR 檔時，回傳空 list"""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        view = create_view(source_root=empty_dir, review_root=None)
        entries = view._load_entries_from_jar_directory()

        assert entries == []

    def test_progress_callback_called(self, tmp_path):
        """processed_callback 應該被呼叫"""
        jar_dir = tmp_path / "mods"
        jar_dir.mkdir()

        create_test_jar(jar_dir, "mod.jar", {
            "assets/mod/lang/en_us.json": json.dumps({"key": "value"}),
        })

        callback_calls = []

        def progress_callback():
            callback_calls.append(1)

        view = create_view(source_root=jar_dir, review_root=None)
        entries = view._load_entries_from_jar_directory(
            processed_callback=progress_callback
        )

        # callback 應該至少被呼叫一次（墊底 + 至少一個 JAR 完成後）
        assert len(callback_calls) >= 1

    def test_source_jar_attribute_set(self, tmp_path):
        """entries 應該包含 source_jar 屬性"""
        jar_dir = tmp_path / "mods"
        jar_dir.mkdir()

        create_test_jar(jar_dir, "actuallyadditions-1.20.jar", {
            "assets/actuallyadditions/lang/en_us.json": json.dumps({
                "item.actuallyadditions.atomic_reconstructor": "Atomic Reshaper",
            }),
        })

        view = create_view(source_root=jar_dir, review_root=None)
        entries = view._load_entries_from_jar_directory()

        assert len(entries) == 1
        assert entries[0].source_jar == "actuallyadditions-1.20.jar"

    def test_nested_jar_structure(self, tmp_path):
        """JAR 內有多個 en_us.json（多個模組在同一個 JAR）"""
        jar_dir = tmp_path / "mods"
        jar_dir.mkdir()

        create_test_jar(jar_dir, "kjs-etc.jar", {
            "assets/kubejs/lang/en_us.json": json.dumps({"kubejs.key": "kubejs value"}),
            "assets/create/lang/en_us.json": json.dumps({"create.key": "create value"}),
        })

        view = create_view(source_root=jar_dir, review_root=None)
        entries = view._load_entries_from_jar_directory()

        assert len(entries) == 2
        modids = {e.modid for e in entries}
        assert modids == {"kubejs", "create"}
