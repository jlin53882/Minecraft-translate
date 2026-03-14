"""KubeJS pipeline service wrappers."""

from __future__ import annotations

from app.services_impl.logging_service import UI_LOG_HANDLER

def run_kubejs_tooltip_service(
    input_dir: str,
    session,
    output_dir: str | None,
    dry_run: bool = False,
    step_extract: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,
):
    """執行 KubeJS 翻譯流程"""
    from app.services_impl.pipelines._task_runner import run_callable_task
    from translation_tool.core.kubejs_translator import run_kubejs_pipeline

    return run_callable_task(
        session=session,
        task_name="致命錯誤] KubeJS 服務失敗：",
        func=run_kubejs_pipeline,
        kwargs={
            "input_dir": input_dir,
            "output_dir": output_dir,
            "session": session,
            "dry_run": dry_run,
            "step_extract": step_extract,
            "step_translate": step_translate,
            "step_inject": step_inject,
            "write_new_cache": write_new_cache,
        },
        add_session_log_on_error=False,
        ui_log_handler=UI_LOG_HANDLER,
    )
