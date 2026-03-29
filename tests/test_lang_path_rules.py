"""translation_tool/plugins/shared/lang_path_rules.py 模組測試。

用途：測試 lang_path_rules 模組的功能。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
from translation_tool.plugins.shared import lang_path_rules


def test_should_rename_to_zh_tw_true(tmp_path: Path) -> None:
    """測試 should_rename_to_zh_tw 回傳 True 的情況。"""
    # 測試 en_us.json
    path = Path("en_us.json")
    rename_langs = {"en_us", "zh_cn", "zh_tw"}
    
    assert lang_path_rules.should_rename_to_zh_tw(path, rename_langs) is True


def test_should_rename_to_zh_tw_false_not_json(tmp_path: Path) -> None:
    """測試 should_rename_to_zh_tw 不是 JSON 檔案的情況。"""
    path = Path("en_us.txt")
    rename_langs = {"en_us", "zh_cn", "zh_tw"}
    
    assert lang_path_rules.should_rename_to_zh_tw(path, rename_langs) is False


def test_should_rename_to_zh_tw_false_not_lang_code(tmp_path: Path) -> None:
    """測試 should_rename_to_zh_tw 不是語系代碼的情況。"""
    path = Path("something.json")
    rename_langs = {"en_us", "zh_cn", "zh_tw"}
    
    assert lang_path_rules.should_rename_to_zh_tw(path, rename_langs) is False


def test_should_rename_to_zh_tw_case_insensitive(tmp_path: Path) -> None:
    """測試 should_rename_to_zh_tw 大小寫不敏感。"""
    path = Path("EN_US.json")
    rename_langs = {"en_us", "zh_cn", "zh_tw"}
    
    assert lang_path_rules.should_rename_to_zh_tw(path, rename_langs) is True


def test_should_rename_to_zh_tw_not_in_rename_langs(tmp_path: Path) -> None:
    """測試 should_rename_to_zh_tw 不在 rename_langs 中。"""
    path = Path("ru_ru.json")
    rename_langs = {"en_us", "zh_cn", "zh_tw"}
    
    assert lang_path_rules.should_rename_to_zh_tw(path, rename_langs) is False


def test_is_lang_code_segment_true(tmp_path: Path) -> None:
    """測試 is_lang_code_segment 回傳 True 的情況。"""
    assert lang_path_rules.is_lang_code_segment("en_us") is True
    assert lang_path_rules.is_lang_code_segment("zh_cn") is True
    assert lang_path_rules.is_lang_code_segment("zh_tw") is True


def test_is_lang_code_segment_false(tmp_path: Path) -> None:
    """測試 is_lang_code_segment 回傳 False 的情況。"""
    assert lang_path_rules.is_lang_code_segment("en_us_extra") is False
    assert lang_path_rules.is_lang_code_segment("enus") is False
    assert lang_path_rules.is_lang_code_segment("en") is False
    assert lang_path_rules.is_lang_code_segment("readme") is False
    assert lang_path_rules.is_lang_code_segment("123") is False


def test_is_lang_code_segment_case_insensitive(tmp_path: Path) -> None:
    """測試 is_lang_code_segment 大小寫不敏感。"""
    assert lang_path_rules.is_lang_code_segment("EN_US") is True
    assert lang_path_rules.is_lang_code_segment("Zh_Cn") is True


def test_replace_lang_folder_with_zh_tw(tmp_path: Path) -> None:
    """測試 replace_lang_folder_with_zh_tw 替換語系資料夾。"""
    rel = Path("config") / "lang" / "en_us" / "file.json"
    
    result = lang_path_rules.replace_lang_folder_with_zh_tw(rel)
    
    assert result == Path("config") / "lang" / "zh_tw" / "file.json"


def test_replace_lang_folder_with_zh_tw_multiple_segments(tmp_path: Path) -> None:
    """測試 replace_lang_folder_with_zh_tw 多個語系段落。"""
    rel = Path("en_us") / "sub" / "zh_cn" / "file.json"
    
    result = lang_path_rules.replace_lang_folder_with_zh_tw(rel)
    
    # 應該只替換第一個遇到的語系段落
    assert "zh_tw" in result.parts


def test_replace_lang_folder_with_zh_tw_no_lang(tmp_path: Path) -> None:
    """測試 replace_lang_folder_with_zh_tw 無語系資料夾。"""
    rel = Path("config") / "file.json"
    
    result = lang_path_rules.replace_lang_folder_with_zh_tw(rel)
    
    assert result == Path("config") / "file.json"


def test_compute_output_path_basic(tmp_path: Path) -> None:
    """測試 compute_output_path 基本功能。"""
    src_path = Path("lang") / "en_us.json"
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    rename_langs = {"en_us"}
    
    in_dir.mkdir(parents=True)
    (in_dir / src_path).parent.mkdir(parents=True)
    (in_dir / src_path).write_text("{}")
    
    result = lang_path_rules.compute_output_path(
        in_dir / src_path, in_dir, out_dir, rename_langs
    )
    
    # 應該替換語系資料夾和檔名
    result_str = str(result)
    assert "zh_tw" in result_str


def test_compute_output_path_preserve_structure(tmp_path: Path) -> None:
    """測試 compute_output_path 保留目錄結構。"""
    src_path = Path("subdir1") / "subdir2" / "en_us.json"
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    rename_langs = {"en_us"}
    
    in_dir.mkdir(parents=True)
    full_src = in_dir / src_path
    full_src.parent.mkdir(parents=True)
    full_src.write_text("{}")
    
    result = lang_path_rules.compute_output_path(
        full_src, in_dir, out_dir, rename_langs
    )
    
    assert "subdir1" in result.parts
    assert "subdir2" in result.parts
    assert "zh_tw.json" == result.name


def test_compute_output_path_no_rename_needed(tmp_path: Path) -> None:
    """測試 compute_output_path 不需要改名的情况。"""
    src_path = Path("lang") / "custom.json"  # 不是語系代碼
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    rename_langs = {"en_us"}
    
    in_dir.mkdir(parents=True)
    full_src = in_dir / src_path
    full_src.parent.mkdir(parents=True)
    full_src.write_text("{}")
    
    result = lang_path_rules.compute_output_path(
        full_src, in_dir, out_dir, rename_langs
    )
    
    # 應該保留原檔名
    assert result.name == "custom.json"
