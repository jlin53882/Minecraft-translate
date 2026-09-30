"""專案根目錄的 pytest 設定：只放與測試目錄無關的全域 fixture。

tests/conftest.py 保持原樣（其中有既有的 lint 問題，避免動到它就讓 CI 變紅）。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_lm_batch_budget():
    """每個測試前後清掉翻譯批次 token 預算的學習狀態（模組層級全域）。

    預算依 profile 保存在模組內、跨呼叫保留（這是功能本身，issue #108），
    測試之間必須隔離，否則某個測試觸發的截斷會讓後面測試的批次變小。
    """
    from translation_tool.core.lm_batch_budget import reset_trackers

    reset_trackers()
    yield
    reset_trackers()
