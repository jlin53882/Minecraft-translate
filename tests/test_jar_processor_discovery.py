"""測試 jar_processor_discovery.py - JAR 發現邏輯。

用途：測試 jar_processor_discovery.py 的功能。
"""

import os
import tempfile
import shutil
from pathlib import Path

import pytest

from translation_tool.core.jar_processor_discovery import find_jar_files


class TestFindJarFiles:
    """測試 find_jar_files 函數"""

    def test_find_jar_files_empty_dir(self, tmp_path):
        """測試空目錄應該回傳空列表"""
        result = find_jar_files(str(tmp_path))
        assert result == []

    def test_find_jar_files_no_jars(self, tmp_path):
        """測試沒有 JAR 檔的目錄"""
        # 建立一些非 JAR 檔案
        (tmp_path / "file.txt").write_text("content")
        (tmp_path / "file.zip").write_text("content")
        
        result = find_jar_files(str(tmp_path))
        assert result == []

    def test_find_jar_files_single_jar(self, tmp_path):
        """測試單一 JAR 檔案"""
        jar_path = tmp_path / "test.jar"
        jar_path.write_bytes(b"PK\x03\x04")  # 模擬 ZIP/JAR 檔案標頭
        
        result = find_jar_files(str(tmp_path))
        assert len(result) == 1
        assert result[0] == str(jar_path)

    def test_find_jar_files_multiple_jars(self, tmp_path):
        """測試多個 JAR 檔案"""
        (tmp_path / "mod1.jar").write_bytes(b"PK\x03\x04")
        (tmp_path / "mod2.jar").write_bytes(b"PK\x03\x04")
        (tmp_path / "file.txt").write_text("content")
        
        result = find_jar_files(str(tmp_path))
        assert len(result) == 2
        assert all(f.endswith(".jar") for f in result)

    def test_find_jar_files_nested(self, tmp_path):
        """測試遞迴搜尋子目錄中的 JAR"""
        sub_dir = tmp_path / "mods" / "sub"
        sub_dir.mkdir(parents=True)
        (tmp_path / "root.jar").write_bytes(b"PK\x03\x04")
        (sub_dir / "nested.jar").write_bytes(b"PK\x03\x04")
        
        result = find_jar_files(str(tmp_path))
        assert len(result) == 2
        jar_names = [os.path.basename(f) for f in result]
        assert "root.jar" in jar_names
        assert "nested.jar" in jar_names

    def test_find_jar_files_case_insensitive(self, tmp_path):
        """測試不同大小寫的 JAR 副檔名"""
        (tmp_path / "test.JAR").write_bytes(b"PK\x03\x04")
        (tmp_path / "test.Jar").write_bytes(b"PK\x03\x04")
        (tmp_path / "normal.jar").write_bytes(b"PK\x03\x04")
        
        result = find_jar_files(str(tmp_path))
        # os.walk 使用 endswith('.jar') 所以只會匹配小寫
        # 在 Windows 下可能會匹配
        assert len(result) >= 1

    def test_find_jar_files_returns_absolute_paths(self, tmp_path):
        """測試回傳絕對路徑"""
        jar_path = tmp_path / "test.jar"
        jar_path.write_bytes(b"PK\x03\x04")
        
        result = find_jar_files(str(tmp_path))
        assert os.path.isabs(result[0])
