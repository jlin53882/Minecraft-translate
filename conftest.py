"""專案根目錄的 pytest 設定：只放與測試目錄無關的全域 fixture。

tests/conftest.py 保持原樣（其中有既有的 lint 問題，避免動到它就讓 CI 變紅）。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_lm_global_state():
    """每個測試前後清掉翻譯引擎的跨呼叫學習狀態（模組層級全域）。

    - token 預算（issue #108）：依 profile 保存，跨批次保留。
    - API Key 健康狀態（issue #113）：已確定耗盡的 key 會冷卻一段時間。

    這些都是功能本身要保留的狀態，所以測試之間必須隔離，否則某個測試觸發的截斷或 key 耗盡
    會讓後面測試的行為改變。
    """
    from translation_tool.core.lm_batch_budget import reset_trackers
    from translation_tool.core.lm_key_health import reset_key_health

    reset_trackers()
    reset_key_health()
    yield
    reset_trackers()
    reset_key_health()
