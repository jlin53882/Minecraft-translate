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


@pytest.fixture(autouse=True)
def _reset_ui_mirror_task_context():
    """每個測試前後清掉「目前任務」的 context（模組層級的 ContextVar）。

    服務入口（``UI_LOG_HANDLER.set_session``）會把目前執行緒歸屬到某個任務；沒清掉的話，
    同一個 worker 後面執行的測試寫出的後台記錄會帶著別的測試的任務識別，
    UI→後台鏡像去重的測試就會因為「歸屬不相容」而偶發失敗。
    """
    from translation_tool.utils import ui_mirror

    ui_mirror.set_current_task(None)
    yield
    ui_mirror.set_current_task(None)
