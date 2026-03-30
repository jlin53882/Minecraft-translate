"""tests/test_icon_preview_mode_detection.py

測試 icon_preview_view 的模式偵測功能。
驗證 _detect_source_mode() 能正確區分：
  - jar_directory：全是 JAR，無 en_us.json
  - extracted_folder：有 en_us.json
  - empty：兩者都沒有
  - unknown：source_root 為 None
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import flet as ft


class MockPage:
    def __init__(self):
        self.overlay = []


def create_view_with_source_root(source_root: Path | None):
    """建立 IconPreviewView 並設定 source_root"""
    from app.views.icon_preview_view import IconPreviewView

    with patch.object(IconPreviewView, "__init__", lambda self, page: None):
        view = IconPreviewView.__new__(IconPreviewView)
        view.page = MockPage()
        view.source_root = source_root
    return view


class TestDetectSourceMode:
    """_detect_source_mode 的各種情境測試"""

    def test_detect_jar_directory_mode(self, tmp_path):
        """JAR 檔存在、en_us.json 不存在 → jar_directory"""
        jar_dir = tmp_path / "mods"
        jar_dir.mkdir()
        (jar_dir / "mod1.jar").touch()
        (jar_dir / "mod2.jar").touch()

        view = create_view_with_source_root(jar_dir)
        mode = view._detect_source_mode()

        assert mode == "jar_directory", f"預期 'jar_directory'，實際 '{mode}'"

    def test_detect_extracted_folder_mode(self, tmp_path):
        """en_us.json 存在 → extracted_folder"""
        extracted_dir = tmp_path / "extracted"
        assets_dir = extracted_dir / "assets" / "actuallyadditions" / "lang"
        assets_dir.mkdir(parents=True)
        (assets_dir / "en_us.json").write_text("{}", encoding="utf-8")

        view = create_view_with_source_root(extracted_dir)
        mode = view._detect_source_mode()

        assert mode == "extracted_folder", f"預期 'extracted_folder'，實際 '{mode}'"

    def test_detect_empty_mode_no_jars_no_lang(self, tmp_path):
        """兩者都沒有 → empty"""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        view = create_view_with_source_root(empty_dir)
        mode = view._detect_source_mode()

        assert mode == "empty", f"預期 'empty'，實際 '{mode}'"

    def test_detect_empty_mode_with_readme(self, tmp_path):
        """只有 README.txt，沒有 JAR 也沒有 en_us.json → empty"""
        empty_dir = tmp_path / "emptytwo"
        empty_dir.mkdir()
        (empty_dir / "README.txt").write_text("...", encoding="utf-8")

        view = create_view_with_source_root(empty_dir)
        mode = view._detect_source_mode()

        assert mode == "empty", f"預期 'empty'，實際 '{mode}'"

    def test_detect_unknown_mode_none_source_root(self):
        """source_root 為 None → unknown"""
        view = create_view_with_source_root(None)
        mode = view._detect_source_mode()

        assert mode == "unknown", f"預期 'unknown'，實際 '{mode}'"

    def test_detect_jar_directory_with_nested_en_us(self, tmp_path):
        """JAR 存在時，即使有 en_us.json（在其他位置）仍應判斷為 jar_directory"""
        jar_dir = tmp_path / "mods"
        jar_dir.mkdir()
        (jar_dir / "fabric-api.jar").touch()

        # en_us.json 在子資料夾（不是 mods 本身的 assets）
        nested = jar_dir / "subfolder" / "assets" / "mod" / "lang"
        nested.mkdir(parents=True)
        (nested / "en_us.json").write_text("{}", encoding="utf-8")

        view = create_view_with_source_root(jar_dir)
        mode = view._detect_source_mode()

        # 邏輯：jar_count > 0 AND extracted_count == 0 → jar_directory
        # 由於 en_us.json 存在於 rglob，所以 extracted_count > 0
        # 結果應該是 extracted_folder（rglob 有找到 en_us.json）
        assert mode == "extracted_folder", f"en_us.json 存在，應為 extracted_folder，實際為 '{mode}'"

    def test_detect_both_jars_and_en_us(self, tmp_path):
        """同時有 JAR 和 en_us.json → extracted_folder（en_us 優先）"""
        mixed_dir = tmp_path / "mixed"
        mixed_dir.mkdir()
        (mixed_dir / "mod.jar").touch()

        assets_dir = mixed_dir / "assets" / "mod" / "lang"
        assets_dir.mkdir(parents=True)
        (assets_dir / "en_us.json").write_text("{}", encoding="utf-8")

        view = create_view_with_source_root(mixed_dir)
        mode = view._detect_source_mode()

        assert mode == "extracted_folder", f"預期 'extracted_folder'，實際 '{mode}'"
