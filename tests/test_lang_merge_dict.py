"""Unit tests for translation_tool.core.lang_merge_dict (2026-08-02)。"""

import pytest

from translation_tool.core.lang_merge_dict import merge_lang_dicts, contains_cjk, is_pure_english


# 假的 helper (代替 text_processor 的 callable)
def fake_replace_rules(s, rules):
    return s


def fake_recursive_translate_dict(v, rules):
    return v


class TestMergeLangDicts:
    """測 merge_lang_dicts 純函式的 5 個規則。"""

    def test_rule_1_artificial_zh_tw_protected(self):
        """1. 既有 zh_tw 含 CJK (人工翻譯) → 跳過不覆寫。"""
        existing = {"k_old": "人工翻譯中文"}
        cn_data = {"k_old": "原文"}
        final_tw, pending = merge_lang_dicts(
            cn_data=cn_data,
            tw_src_data=None,
            en_data={},
            existing_tw=existing,
            rules=[],
            apply_replace_rules=fake_replace_rules,
            recursive_translate_dict=fake_recursive_translate_dict,
            contains_cjk=contains_cjk,
            is_pure_english=is_pure_english,
            is_from_output_dir=True,
        )
        # k_old 保持人工翻譯
        assert final_tw["k_old"] == "人工翻譯中文"
        assert "k_old" not in pending

    def test_rule_2_zh_tw_source_with_cjk_used_with_replace(self):
        """2. zh_tw 來源含 CJK → 用規則處理後寫進 final_tw。"""
        cn_data = {"k": "原文"}
        tw_src = {"k": "原文中文"}
        final_tw, pending = merge_lang_dicts(
            cn_data=cn_data,
            tw_src_data=tw_src,
            en_data={},
            existing_tw=None,
            rules=[],
            apply_replace_rules=fake_replace_rules,
            recursive_translate_dict=fake_recursive_translate_dict,
            contains_cjk=contains_cjk,
            is_pure_english=is_pure_english,
            is_from_output_dir=False,
        )
        # 用了 zh_tw 來源,沒動
        assert final_tw["k"] == "原文中文"

    def test_rule_3_zh_cn_with_cjk_translated(self):
        """3. zh_cn 含 CJK 但 zh_tw 沒 → S2TW 翻譯 (caller 提供 callable)。"""
        cn_data = {"k": "車輛"}
        en_data = {}
        final_tw, pending = merge_lang_dicts(
            cn_data=cn_data,
            tw_src_data=None,
            en_data=en_data,
            existing_tw=None,
            rules=[],
            apply_replace_rules=fake_replace_rules,
            recursive_translate_dict=fake_recursive_translate_dict,  # 假的,原值
            contains_cjk=contains_cjk,
            is_pure_english=is_pure_english,
            is_from_output_dir=False,
        )
        # 看 caller 給的 callable 怎麼處理
        assert final_tw["k"] == "車輛"  # 原值

    def test_rule_4_pure_english_to_pending(self):
        """4. 全英文進 pending。"""
        en_data = {"k_en": "Hello"}
        final_tw, pending = merge_lang_dicts(
            cn_data={},
            tw_src_data=None,
            en_data=en_data,
            existing_tw=None,
            rules=[],
            apply_replace_rules=fake_replace_rules,
            recursive_translate_dict=fake_recursive_translate_dict,
            contains_cjk=contains_cjk,
            is_pure_english=is_pure_english,
            is_from_output_dir=False,
        )
        assert "k_en" in pending
        assert pending["k_en"] == "Hello"

    def test_rule_5_fallback_english(self):
        """5. fallback:都沒 CJK 的話保留 english_source 到 final_tw。"""
        en_data = {"k_mix": "Hello"}
        # 無 zh_cn 也無 zh_tw,純英文進 pending 不進 final_tw
        final_tw, pending = merge_lang_dicts(
            cn_data={},
            tw_src_data=None,
            en_data=en_data,
            existing_tw=None,
            rules=[],
            apply_replace_rules=fake_replace_rules,
            recursive_translate_dict=fake_recursive_translate_dict,
            contains_cjk=contains_cjk,
            is_pure_english=is_pure_english,
            is_from_output_dir=False,
        )
        # 純英文進 pending
        assert "k_mix" in pending


class TestContainsCjk:
    """測 contains_cjk 正確辨識 CJK 字元。"""

    def test_str_with_cjk_returns_true(self):
        assert contains_cjk("中文") is True

    def test_str_pure_english_returns_false(self):
        assert contains_cjk("Hello") is False

    def test_list_with_cjk_returns_true(self):
        assert contains_cjk(["abc", "中文", "def"]) is True

    def test_list_pure_english_returns_false(self):
        assert contains_cjk(["abc", "def"]) is False

    def test_dict_with_cjk_returns_true(self):
        assert contains_cjk({"k1": "中文"}) is True

    def test_dict_pure_english_returns_false(self):
        assert contains_cjk({"k1": "Hello"}) is False

    def test_int_returns_false(self):
        assert contains_cjk(42) is False

    def test_none_returns_false(self):
        assert contains_cjk(None) is False


class TestIsPureEnglish:
    """測 is_pure_english 邏輯。"""

    def test_pure_english_str(self):
        assert is_pure_english("Hello") is True

    def test_cjk_str_returns_false(self):
        assert is_pure_english("中文") is False

    def test_empty_str_returns_false(self):
        """空字串不算 English (沒文字)。"""
        assert is_pure_english("") is False

    def test_whitespace_only_returns_false(self):
        assert is_pure_english("   ") is False

    def test_mixed_structure(self):
        assert is_pure_english({"k1": "Hello", "k2": "World"}) is True
        assert is_pure_english({"k1": "Hello", "k2": "中文"}) is False
