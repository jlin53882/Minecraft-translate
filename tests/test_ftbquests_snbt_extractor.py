"""translation_tool/plugins/ftbquests/ftbquests_snbt_extractor.py 模組測試。

用途：測試 ftbquests_snbt_extractor 模組的功能。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
from translation_tool.plugins.ftbquests import ftbquests_snbt_extractor


def test_is_lang_key_ref(tmp_path: Path) -> None:
    """測試 is_lang_key_ref 判斷 FTB 語系參考。"""
    assert ftbquests_snbt_extractor.is_lang_key_ref("{ftbquests.xxx}") is True
    assert ftbquests_snbt_extractor.is_lang_key_ref("plain text") is False
    assert ftbquests_snbt_extractor.is_lang_key_ref("{atm9.quest}") is False  # 不是 ftbquests


def test_is_lang_key_ref_like(tmp_path: Path) -> None:
    """測試 is_lang_key_ref_like 純引用格式。"""
    assert ftbquests_snbt_extractor.is_lang_key_ref_like("{atm9.quest.create.desc.belts.1}") is True
    assert ftbquests_snbt_extractor.is_lang_key_ref_like("{a}\n{b}") is True
    assert ftbquests_snbt_extractor.is_lang_key_ref_like("plain text") is False
    assert ftbquests_snbt_extractor.is_lang_key_ref_like("") is False


def test_is_tag_condition_text(tmp_path: Path) -> None:
    """測試 is_tag_condition_text 標籤條件文字。"""
    assert ftbquests_snbt_extractor.is_tag_condition_text("Any #minecraft:logs") is True
    assert ftbquests_snbt_extractor.is_tag_condition_text("All #forge:ingots/iron") is True
    assert ftbquests_snbt_extractor.is_tag_condition_text("Normal text") is False


def test_ensure_lang(tmp_path: Path) -> None:
    """測試 ensure_lang 確保語系存在。"""
    store = {}

    ftbquests_snbt_extractor.ensure_lang(store, "en_us")

    assert "en_us" in store
    assert "lang" in store["en_us"]
    assert "quests" in store["en_us"]


def test_ensure_lang_preserves_existing(tmp_path: Path) -> None:
    """測試 ensure_lang 保留現有資料。"""
    store = {"en_us": {"lang": {"existing": "value"}, "quests": {}}}

    ftbquests_snbt_extractor.ensure_lang(store, "en_us")

    assert "existing" in store["en_us"]["lang"]


def test_lang_whitelist_constant(tmp_path: Path) -> None:
    """測試 LANG_WHITELIST 常數。"""
    assert "en_us" in ftbquests_snbt_extractor.LANG_WHITELIST
    assert "zh_cn" in ftbquests_snbt_extractor.LANG_WHITELIST
    assert "zh_tw" in ftbquests_snbt_extractor.LANG_WHITELIST


def test_lang_key_suffix_constant(tmp_path: Path) -> None:
    """測試 LANG_KEY_SUFFIX 常數。"""
    assert ".title" in ftbquests_snbt_extractor.LANG_KEY_SUFFIX
    assert ".quest_desc" in ftbquests_snbt_extractor.LANG_KEY_SUFFIX


def test_lang_priority_constant(tmp_path: Path) -> None:
    """測試 LANG_PRIORITY 常數。"""
    # 應該按照 LANG_WHITELIST 的順序
    assert ftbquests_snbt_extractor.LANG_PRIORITY["en_us"] == 0
    assert ftbquests_snbt_extractor.LANG_PRIORITY["zh_cn"] == 1
    assert ftbquests_snbt_extractor.LANG_PRIORITY["zh_tw"] == 2
