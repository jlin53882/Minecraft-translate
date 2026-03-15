"""測試 icon_classifier.py - 圖示分類邏輯。

用途：測試 icon_classifier.py 的無圖示原因分類功能。
"""

import pytest
from translation_tool.core.icon_classifier import classify_no_icon_reason
from translation_tool.core.icon_reason import IconRisk


class TestClassifyNoIconReason:
    """測試 classify_no_icon_reason 函數"""

    def test_banner_pattern_returns_ignore(self):
        """測試旗幟/樣式相關的 key 應回傳 IGNORE"""
        reason, risk = classify_no_icon_reason("block.minecraft.banner.red_diamond")
        assert risk == IconRisk.IGNORE
        assert "旗幟" in reason or "樣式" in reason

    def test_jei_ui_returns_ignore(self):
        """測試 JEI/Tooltip/ItemGroup 等 UI 相關應回傳 IGNORE"""
        reason, risk = classify_no_icon_reason("jei.category.fuel")
        assert risk == IconRisk.IGNORE
        assert "UI" in reason or "分類文字" in reason

        reason2, risk2 = classify_no_icon_reason("tooltip.stone")
        assert risk2 == IconRisk.IGNORE

    def test_color_status_returns_warn(self):
        """測試顏色/狀態相關應回傳 WARN"""
        reason, risk = classify_no_icon_reason("block.minecraft.light_gray_wool")
        assert risk == IconRisk.WARN
        assert "染色" in reason or "狀態" in reason

    def test_item_block_returns_danger(self):
        """測試 item./block. 開頭應回傳 DANGER"""
        reason, risk = classify_no_icon_reason("item.minecraft.diamond_sword")
        assert risk == IconRisk.DANGER
        assert "物品" in reason or "方塊" in reason

    def test_unknown_returns_warn(self):
        """測試未知類型應回傳 WARN"""
        reason, risk = classify_no_icon_reason("some.random.unknown.key")
        assert risk == IconRisk.WARN
        assert "未知" in reason

    def test_case_insensitive(self):
        """測試大小寫不敏感"""
        reason1, risk1 = classify_no_icon_reason("JEI.category")
        reason2, risk2 = classify_no_icon_reason("jei.CATEGORY")

        assert risk1 == IconRisk.IGNORE
        assert risk2 == IconRisk.IGNORE

    def test_multiple_color_terms(self):
        """測試多個顏色關鍵字"""
        reason, risk = classify_no_icon_reason("block.red_dark_blue_active")
        assert risk == IconRisk.WARN

    def test_misc_prefix(self):
        """測試 misc. 前綴應為 IGNORE"""
        reason, risk = classify_no_icon_reason("misc.some_category")
        assert risk == IconRisk.IGNORE
