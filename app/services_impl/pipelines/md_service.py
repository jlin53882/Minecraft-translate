"""Markdown pipeline service wrappers."""

from __future__ import annotations

from app.services_impl.logging_service import UI_LOG_HANDLER

def run_md_translation_service(
    input_dir: str,
    session,
    output_dir: str | None = None,
    dry_run: bool = False,
    step_extract: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,
    lang_mode: str = "non_cjk_only",
):
    """執行 Markdown 翻譯流程"""
    from app.services_impl.pipelines._task_runner import run_callable_task
    from translation_tool.core.md_translation_assembly import run_md_pipeline

    return run_callable_task(
        session=session,
        task_name="非預期錯誤] MD 流程失敗：",
        func=run_md_pipeline,
        kwargs={
            "input_dir": input_dir,
            "session": session,
            "output_dir": output_dir,
            "dry_run": dry_run,
            "step_extract": step_extract,
            "step_translate": step_translate,
            "step_inject": step_inject,
            "write_new_cache": write_new_cache,
            "lang_mode": lang_mode,
        },
        add_session_log_on_error=True,
        ui_log_handler=UI_LOG_HANDLER,
    )
