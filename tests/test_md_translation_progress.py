"""translation_tool/core/md_translation_progress.py 模組測試。

用途：測試 md_translation_progress 模組的功能。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
from translation_tool.core import md_translation_progress


class MockParent:
    """Mock parent 類別，用於測試 _ProgressProxy。"""

    def __init__(self):
        self.progress_value = None

    def set_progress(self, value: float) -> None:
        self.progress_value = value


def test_progress_proxy_basic(tmp_path: Path) -> None:
    """測試 _ProgressProxy 基本進度映射功能。"""
    parent = MockParent()
    proxy = md_translation_progress._ProgressProxy(parent, base=0.2, span=0.6)

    # 設定 50% 進度（0.5），應該映射到 0.2 + 0.5 * 0.6 = 0.5
    proxy.set_progress(0.5)

    assert parent.progress_value == pytest.approx(0.5)


def test_progress_proxy_full_progress(tmp_path: Path) -> None:
    """測試 _ProgressProxy 滿進度。"""
    parent = MockParent()
    proxy = md_translation_progress._ProgressProxy(parent, base=0.0, span=1.0)

    proxy.set_progress(1.0)

    assert parent.progress_value == 1.0


def test_progress_proxy_zero_progress(tmp_path: Path) -> None:
    """測試 _ProgressProxy 零進度。"""
    parent = MockParent()
    proxy = md_translation_progress._ProgressProxy(parent, base=0.0, span=1.0)

    proxy.set_progress(0.0)

    assert parent.progress_value == 0.0


def test_progress_proxy_clamp_above_one(tmp_path: Path) -> None:
    """測試 _ProgressProxy 限制進度不超過 1.0。"""
    parent = MockParent()
    proxy = md_translation_progress._ProgressProxy(parent, base=0.0, span=1.0)

    proxy.set_progress(1.5)

    assert parent.progress_value == 1.0


def test_progress_proxy_clamp_below_zero(tmp_path: Path) -> None:
    """測試 _ProgressProxy 限制進度不小於 0.0。"""
    parent = MockParent()
    proxy = md_translation_progress._ProgressProxy(parent, base=0.0, span=1.0)

    proxy.set_progress(-0.5)

    assert parent.progress_value == 0.0


def test_progress_proxy_none_handling(tmp_path: Path) -> None:
    """測試 _ProgressProxy 處理 None 值。"""
    parent = MockParent()
    proxy = md_translation_progress._ProgressProxy(parent, base=0.0, span=1.0)

    proxy.set_progress(None)

    # None 應該被視為 0
    assert parent.progress_value == 0.0


def test_progress_proxy_no_parent(tmp_path: Path) -> None:
    """測試 _ProgressProxy 處理無 parent 情況。"""
    proxy = md_translation_progress._ProgressProxy(None, base=0.0, span=1.0)

    # 不應該拋出例外
    proxy.set_progress(0.5)


def test_progress_proxy_no_set_progress_method(tmp_path: Path) -> None:
    """測試 _ProgressProxy 處理 parent 沒有 set_progress 方法。"""
    parent = object()  # 沒有 set_progress 方法
    proxy = md_translation_progress._ProgressProxy(parent, base=0.0, span=1.0)

    # 不應該拋出例外
    proxy.set_progress(0.5)


def test_progress_proxy_custom_base_span(tmp_path: Path) -> None:
    """測試 _ProgressProxy 自訂 base 和 span。"""
    parent = MockParent()
    # 映射範圍 0.3 到 0.8（span=0.5）
    proxy = md_translation_progress._ProgressProxy(parent, base=0.3, span=0.5)

    proxy.set_progress(0.0)   # 0% -> 0.3
    assert parent.progress_value == pytest.approx(0.3)

    proxy.set_progress(1.0)   # 100% -> 0.8
    assert parent.progress_value == pytest.approx(0.8)

    proxy.set_progress(0.5)   # 50% -> 0.55
    assert parent.progress_value == pytest.approx(0.55)


def test_progress_proxy_exception_handling(tmp_path: Path) -> None:
    """測試 _ProgressProxy 處理例外。"""
    def raising_set_progress(value: float) -> None:
        raise RuntimeError("Test error")

    parent = type('Parent', (), {'set_progress': raising_set_progress})()
    proxy = md_translation_progress._ProgressProxy(parent, base=0.0, span=1.0)

    # 不應該拋出例外，應該靜默忽略
    proxy.set_progress(0.5)


import pytest  # 需要用於 approx
