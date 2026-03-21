"""測試 icon_reason.py - 圖示原因與風險類型。

用途：測試 IconRisk enum 和 IconResult dataclass。
"""

from translation_tool.core.icon_reason import IconRisk, IconResult


class TestIconRisk:
    """測試 IconRisk enum"""

    def test_icon_risk_values(self):
        """測試 IconRisk 三種風險等級的值"""
        assert IconRisk.IGNORE.value == "ignore"
        assert IconRisk.WARN.value == "warn"
        assert IconRisk.DANGER.value == "danger"

    def test_icon_risk_all_values(self):
        """測試所有風險值都有定義"""
        all_risks = [risk.value for risk in IconRisk]
        assert "ignore" in all_risks
        assert "warn" in all_risks
        assert "danger" in all_risks


class TestIconResult:
    """測試 IconResult dataclass"""

    def test_icon_result_creation(self):
        """測試 IconResult 正常建立"""
        result = IconResult(
            icon_path="/path/to/icon.png",
            reason="測試原因",
            risk=IconRisk.WARN
        )
        assert result.icon_path == "/path/to/icon.png"
        assert result.reason == "測試原因"
        assert result.risk == IconRisk.WARN

    def test_icon_result_with_none(self):
        """測試 IconResult 可接受 None 值"""
        result = IconResult(
            icon_path=None,
            reason="找不到圖示",
            risk=IconRisk.DANGER
        )
        assert result.icon_path is None
        assert result.reason == "找不到圖示"

    def test_icon_result_with_path_object(self):
        """測試 IconResult 可接受 Path 物件"""
        from pathlib import Path
        result = IconResult(
            icon_path=Path("test.png"),
            reason="",
            risk=None
        )
        assert isinstance(result.icon_path, Path)

    def test_icon_result_with_any_object(self):
        """測試 IconResult icon_path 類型為 Any"""
        result = IconResult(
            icon_path=123,  # 任意物件
            reason="",
            risk=None
        )
        assert result.icon_path == 123

    def test_icon_result_equality(self):
        """測試 IconResult 相等性"""
        result1 = IconResult(icon_path=None, reason="test", risk=IconRisk.IGNORE)
        result2 = IconResult(icon_path=None, reason="test", risk=IconRisk.IGNORE)
        assert result1 == result2
