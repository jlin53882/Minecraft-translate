"""translation_tool/plugins/ftbquests/ftbquests_snbt_inject.py 模組測試。

用途：測試 ftbquests_snbt_inject 模組的功能。
"""
from __future__ import annotations

import sys
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
from translation_tool.plugins.ftbquests import ftbquests_snbt_inject


def test_normalize_config_dir(tmp_path: Path) -> None:
    """測試 _normalize_config_dir 避免重複路徑。"""
    # 正常路徑
    result = ftbquests_snbt_inject._normalize_config_dir("config")
    assert result == "config"
    
    # 重複的 config/config
    result = ftbquests_snbt_inject._normalize_config_dir("config/config")
    assert result == "config"


def test_load_json_dict(tmp_path: Path) -> None:
    """測試 _load_json_dict 載入 JSON。"""
    json_file = tmp_path / "test.json"
    json_file.write_text(json.dumps({"key": "value"}))
    
    result = ftbquests_snbt_inject._load_json_dict(str(json_file))
    
    assert result == {"key": "value"}


def test_load_json_dict_missing_file(tmp_path: Path) -> None:
    """測試 _load_json_dict 檔案不存在。"""
    result = ftbquests_snbt_inject._load_json_dict(str(tmp_path / "nonexistent.json"))
    
    assert result == {}


def test_split_lang_by_source_file(tmp_path: Path) -> None:
    """測試 split_lang_by_source_file 拆分來源檔。"""
    lang_map = {
        "file1.snbt|key1": "value1",
        "file2.snbt|key2": "value2",
        "key3": "value3",  # 沒有檔名
    }
    
    result = ftbquests_snbt_inject.split_lang_by_source_file(lang_map)
    
    assert "file1.snbt" in result
    assert "file2.snbt" in result
    assert "_default" in result
    assert result["file1.snbt"]["key1"] == "value1"


def test_split_lang_by_source_file_with_list(tmp_path: Path) -> None:
    """測試 split_lang_by_source_file 處理列表值。"""
    lang_map = {
        "file1.snbt|key1": ["value1", "value2"],
    }
    
    result = ftbquests_snbt_inject.split_lang_by_source_file(lang_map)
    
    assert "file1.snbt" in result
    assert result["file1.snbt"]["key1"] == ["value1", "value2"]


def test_split_lang_by_source_file_invalid_value(tmp_path: Path) -> None:
    """測試 split_lang_by_source_file 跳過無效值。"""
    lang_map = {
        "key1": 123,  # 不是字串或列表
        "key2": ["valid", 456],  # 列表中有非字串
    }
    
    result = ftbquests_snbt_inject.split_lang_by_source_file(lang_map)
    
    assert "key1" not in result.get("_default", {})


def test_walk_and_copy_all_snbt(tmp_path: Path) -> None:
    """測試 walk_and_copy_all_snbt 複製 SNBT 檔案。"""
    # 建立來源目錄結構
    src_root = tmp_path / "src"
    src_root.mkdir()
    (src_root / "file1.snbt").write_text("content1")
    subdir = src_root / "sub"
    subdir.mkdir()
    (subdir / "file2.snbt").write_text("content2")
    
    # 建立目標目錄
    dst_root = tmp_path / "dst"
    
    # 執行
    count = ftbquests_snbt_inject.walk_and_copy_all_snbt(str(src_root), str(dst_root))
    
    assert count == 2
    assert (dst_root / "file1.snbt").exists()
    assert (dst_root / "sub" / "file2.snbt").exists()


def test_walk_and_copy_all_snbt_creates_parent(tmp_path: Path) -> None:
    """測試 walk_and_copy_all_snbt 建立父目錄。"""
    src_root = tmp_path / "src"
    src_root.mkdir()
    (src_root / "nested" / "file.snbt").parent.mkdir(parents=True)
    (src_root / "nested" / "file.snbt").write_text("content")
    
    dst_root = tmp_path / "dst"
    
    count = ftbquests_snbt_inject.walk_and_copy_all_snbt(str(src_root), str(dst_root))
    
    assert count == 1
    assert (dst_root / "nested" / "file.snbt").exists()


def test_patch_lang_snbt_file_basic(tmp_path: Path) -> None:
    """測試 patch_lang_snbt_file 基本功能（需要 mock SNBT 庫）。"""
    # 由於 SNBT 庫可能不可用，這裡測試錯誤處理
    result = ftbquests_snbt_inject.patch_lang_snbt_file(
        str(tmp_path / "nonexistent.snbt"),
        str(tmp_path / "output.snbt"),
        {"key": "value"},
    )
    
    # 應該回傳 (0, 0) 表示沒有變更
    assert result[0] == 0
    assert result[1] == 0
