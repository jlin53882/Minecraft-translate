"""Markdown pipeline service wrappers."""

from __future__ import annotations

from app.services_impl.logging_service import UI_LOG_HANDLER
from translation_tool.utils.config_manager import load_config


def get_md_use_zh_cn_default() -> bool:
    """取得 Markdown 翻譯頁「使用 zh_cn 當底稿」開關的預設值。"""
    return bool(load_config().get("translator", {}).get("md_use_zh_cn_base", True))


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
    use_zh_cn: bool = True,
):
    """執行 Markdown 翻譯流程"""
    from app.services_impl.pipelines._task_runner import run_callable_task
    from translation_tool.core.md_translation_assembly import run_md_pipeline
    from translation_tool.core.plugin_resume import resume_task

    with resume_task(
        "md",
        input_dir=input_dir,
        output_dir=output_dir,
        options={
            "step_extract": step_extract,
            "step_translate": step_translate,
            "step_inject": step_inject,
            "write_new_cache": write_new_cache,
            "lang_mode": lang_mode,
            "use_zh_cn": use_zh_cn,
        },
        session=session,
        dry_run=dry_run,
        translate_enabled=step_translate,
    ):
        return run_callable_task(
            session=session,
            task_name="[非預期錯誤] MD 流程失敗：",
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
                "use_zh_cn": use_zh_cn,
            },
            add_session_log_on_error=True,
            ui_log_handler=UI_LOG_HANDLER,
        )
