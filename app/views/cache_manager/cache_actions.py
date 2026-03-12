from __future__ import annotations

import time
import traceback
from typing import Callable


def run_cache_action(view, reason: str, work_fn: Callable, success_msg: str):
    if view.ui_busy:
        view._notify("目前正在處理，請稍候", "warn")
        return

    action_id = int(time.time() * 1000) % 1000000
    view._append_log(f"[ACTION#{action_id}] start {reason}")
    view._set_state(True, reason, f"trace: ACTION#{action_id} start {reason}")

    try:
        data = work_fn()
        view._refresh_overview_ui(data)
        view._refresh_query_type_options()
        view._render_query_type_shard_page()
        view._append_log(f"[ACTION#{action_id}] success {reason}")
        view._notify(success_msg, "info")
    except Exception as ex:
        view._append_log(f"[ACTION#{action_id}] error {reason}: {ex}")
        view._append_log(traceback.format_exc())
        view._notify(f"{reason} 失敗: {ex}", "error")
    finally:
        view._append_log(f"[ACTION#{action_id}] finish READY")
        view._set_state(False, "READY", f"trace: ACTION#{action_id} ready")
        view._append_log(f"[STATE] {view.overview_status.value}")
