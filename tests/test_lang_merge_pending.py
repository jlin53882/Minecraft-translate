"""translation_tool/core/lang_merge_pending.py 模組測試。

用途：測試 lang_merge_pending 模組的功能。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
import translation_tool.core.lang_merge_pending as lang_merge_pending


class MockJsonModule:
    """Mock json 模組，用於測試。"""

    @staticmethod
    def loads(data):
        import json
        return json.loads(data)

    @staticmethod
    def dumps(data, **kwargs):
        import json
        return json.dumps(data, **kwargs)

    OPT_INDENT_2 = None


def test_remove_empty_dirs_impl_basic(tmp_path: Path) -> None:
    """測試 remove_empty_dirs_impl 基本功能：刪除空目錄。"""
    # 建立測試目錄結構
    test_dir = tmp_path / "test"
    sub_dir = test_dir / "empty_subdir"
    sub_dir.mkdir(parents=True)

    # 確認目錄存在
    assert sub_dir.exists()

    # 執行函式
    lang_merge_pending.remove_empty_dirs_impl(str(test_dir))

    # 驗證：空目錄應該被刪除
    assert not sub_dir.exists()
    assert test_dir.exists()


def test_remove_empty_dirs_impl_keeps_non_empty(tmp_path: Path) -> None:
    """測試 remove_empty_dirs_impl 保留非空目錄。"""
    test_dir = tmp_path / "test"
    sub_dir = test_dir / "non_empty"
    sub_dir.mkdir(parents=True)

    # 在子目錄中建立檔案
    (sub_dir / "file.txt").write_text("content")

    # 執行函式
    lang_merge_pending.remove_empty_dirs_impl(str(test_dir))

    # 驗證：非空目錄應該保留
    assert sub_dir.exists()
    assert (sub_dir / "file.txt").exists()


def test_remove_empty_dirs_impl_nonexistent_root(tmp_path: Path) -> None:
    """測試 remove_empty_dirs_impl 處理不存在的根目錄。"""
    nonexistent = tmp_path / "nonexistent"

    # 不應該拋出例外
    lang_merge_pending.remove_empty_dirs_impl(str(nonexistent))


def test_remove_empty_dirs_impl_with_logger(tmp_path: Path) -> None:
    """測試 remove_empty_dirs_impl 使用自訂 logger。"""
    test_dir = tmp_path / "test"
    sub_dir = test_dir / "empty"
    sub_dir.mkdir(parents=True)

    MagicMock()

    lang_merge_pending.remove_empty_dirs_impl(str(test_dir), )

    assert not sub_dir.exists()


def test_export_filtered_pending_impl_basic(tmp_path: Path) -> None:
    """測試 export_filtered_pending_impl 基本功能。"""
    import orjson

    pending_root = tmp_path / "pending"
    output_root = tmp_path / "output"

    # 建立符合門檻的檔案（2 筆資料 >= min_count=2）
    keep_file = pending_root / "keep.json"
    # skip_file 需要在不同的 parent 目錄以避免衝突
    skip_file = tmp_path / "skip_dir" / "skip.json"

    keep_file.parent.mkdir(parents=True, exist_ok=True)
    skip_file.parent.mkdir(parents=True, exist_ok=True)

    keep_file.write_bytes(orjson.dumps({"key1": "value1", "key2": "value2"}))
    skip_file.write_bytes(orjson.dumps({"key1": "value1"}))

    # 執行函式
    lang_merge_pending.export_filtered_pending_impl(
        str(pending_root),
        str(output_root),
        min_count=2,
        json_module=orjson,
    )

    # 驗證：符合門檻的檔案應該被輸出
    assert (output_root / "keep.json").exists()
    # 不符合門檻的檔案不應該被輸出（但目錄結構會被建立）
    # 舊輸出應該被清除
    assert not (output_root / "skip.json").exists() or not (output_root / "skip.json").read_bytes()


def test_export_filtered_pending_impl_cleans_old_output(tmp_path: Path) -> None:
    """測試 export_filtered_pending_impl 會清除舊輸出。"""
    import orjson

    pending_root = tmp_path / "pending"
    output_root = tmp_path / "output"

    # 先建立舊輸出
    old_file = output_root / "old.json"
    old_file.parent.mkdir(parents=True)
    old_file.write_text("old content")

    # 建立新的 pending 檔案
    keep_file = pending_root / "keep.json"
    keep_file.parent.mkdir(parents=True)
    keep_file.write_bytes(orjson.dumps({"key": "value"}))

    # 執行函式
    lang_merge_pending.export_filtered_pending_impl(
        str(pending_root),
        str(output_root),
        min_count=1,
        json_module=orjson,
    )

    # 驗證：舊輸出被清除
    assert not old_file.exists()


def test_export_filtered_pending_impl_invalid_json(tmp_path: Path) -> None:
    """測試 export_filtered_pending_impl 處理無效 JSON。"""
    pending_root = tmp_path / "pending"
    output_root = tmp_path / "output"

    # 建立無效的 JSON 檔案
    bad_file = pending_root / "bad.json"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text("invalid json {")

    # 執行函式（不應該拋出例外）
    lang_merge_pending.export_filtered_pending_impl(
        str(pending_root),
        str(output_root),
        min_count=1,
        json_module=MockJsonModule(),
    )


def test_export_filtered_pending_impl_preserves_structure(tmp_path: Path) -> None:
    """測試 export_filtered_pending_impl 保留目錄結構。"""
    import orjson

    pending_root = tmp_path / "pending"
    output_root = tmp_path / "output"

    # 建立嵌套結構
    nested_file = pending_root / "subdir" / "nested.json"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_bytes(orjson.dumps({"key": "value"}))

    # 執行函式
    lang_merge_pending.export_filtered_pending_impl(
        str(pending_root),
        str(output_root),
        min_count=1,
        json_module=orjson,
    )

    # 驗證：目錄結構被保留
    assert (output_root / "subdir" / "nested.json").exists()
