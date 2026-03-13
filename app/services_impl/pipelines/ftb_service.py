"""FTB pipeline service wrappers."""

from __future__ import annotations

from app.services_impl.logging_service import UI_LOG_HANDLER

def run_ftb_translation_service(
    directory_path: str,
    session,
    output_dir: str | None,
    dry_run: bool = False,
    step_export: bool = True,
    step_clean: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,
):
    from app.services_impl.pipelines._task_runner import run_callable_task
    from translation_tool.core.ftb_translator import run_ftb_pipeline

    return run_callable_task(
        session=session,
        task_name="致命錯誤] FTB 服務失敗：",
        func=run_ftb_pipeline,
        kwargs={
            "directory_path": directory_path,
            "session": session,
            "output_dir": output_dir,
            "dry_run": dry_run,
            "step_export": step_export,
            "step_clean": step_clean,
            "step_translate": step_translate,
            "step_inject": step_inject,
            "write_new_cache": write_new_cache,
        },
        add_session_log_on_error=True,
        ui_log_handler=UI_LOG_HANDLER,
    )
