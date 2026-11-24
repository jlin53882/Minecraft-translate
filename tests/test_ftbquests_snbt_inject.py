"""translation_tool/plugins/ftbquests/ftbquests_snbt_inject.py 模組測試。

用途：測試 ftbquests_snbt_inject 模組的功能。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
from translation_tool.plugins.ftbquests import ftbquests_snbt_inject  # noqa: E402


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


def test_patch_lang_snbt_file_writes_only_template_keys(tmp_path: Path) -> None:
    """確認 SNBT 注入只改模板已有欄位，並保留其他資料。"""
    source = tmp_path / "en_us.snbt"
    output = tmp_path / "zh_tw.snbt"
    original = '{ title: "English", keep: "unchanged" }'
    source.write_text(original, encoding="utf-8")
    output.write_text(original, encoding="utf-8")

    changed, candidates, details = ftbquests_snbt_inject.patch_lang_snbt_file(
        str(source),
        str(output),
        {"title": "繁體中文", "missing": "不應新增"},
        return_details=True,
    )

    import ftb_snbt_lib as snbt

    with output.open(encoding="utf-8") as stream:
        result = snbt.load(stream)
    assert changed == candidates == 1
    assert str(result["title"]) == "繁體中文"
    assert str(result["keep"]) == "unchanged"
    assert "missing" not in result
    assert details["changed_keys"] == ["title"]
    assert details["missing_in_template"] == ["missing"]


def test_patch_lang_snbt_file_rejects_existing_malformed_target(
    tmp_path: Path,
) -> None:
    """既有繁中 SNBT 損壞時不可靜默退回英文模板覆蓋。"""
    source = tmp_path / "en_us.snbt"
    output = tmp_path / "zh_tw.snbt"
    source.write_text('{ title: "English", keep: "English keep" }', encoding="utf-8")
    output.write_text('{ title: "繁中", broken: }', encoding="utf-8")

    with pytest.raises(ValueError, match="既有目標 SNBT 無法解析"):
        ftbquests_snbt_inject.patch_lang_snbt_file(
            str(source), str(output), {"title": "新的繁中"}
        )


def test_patch_lang_snbt_file_tolerates_trailing_extra_brace(tmp_path: Path) -> None:
    """來源與既有目標結尾多餘的 } 與掃描端一致地忽略；輸出檔括號平衡。"""
    import ftb_snbt_lib as snbt

    source = tmp_path / "en_us.snbt"
    output = tmp_path / "zh_tw.snbt"
    source.write_text('{ title: "English", keep: "Keep" }\n}\n', encoding="utf-8")
    output.write_text('{ title: "舊", keep: "保留" }\n}\n', encoding="utf-8")
    source_before = source.read_bytes()

    changed, candidates = ftbquests_snbt_inject.patch_lang_snbt_file(
        str(source), str(output), {"title": "繁體中文"}
    )

    with output.open(encoding="utf-8") as stream:
        result = snbt.load(stream)  # 嚴格解析成功＝輸出已不含多餘的 }
    assert (changed, candidates) == (1, 1)
    assert str(result["title"]) == "繁體中文" and str(result["keep"]) == "保留"
    assert source.read_bytes() == source_before
