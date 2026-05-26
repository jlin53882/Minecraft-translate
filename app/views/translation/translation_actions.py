from __future__ import annotations

import threading

from translation_tool.utils.log_unit import log_error, log_warning
import time

import flet as ft

from app.logging import LogPresenter, load_ui_logging_config
from translation_tool.utils.config_manager import load_config


# =========================================================
# 執行緒安全的 UI 更新包裝函式（ATK-004 / ATK-017 修復）
# =========================================================
def _safe_add_log(view, message: str):
    """執行緒安全地寫入 session log。view 已卸載時靜靜忽略。"""
    try:
        if hasattr(view, "session") and view.session is not None:
            view.session.add_log(message)
    except Exception:
        pass  # view 已卸載或 session 已 GC，忽略


def _safe_page_update(view):
    """執行緒安全地更新 page。view 已卸載時靜靜忽略。"""
    try:
        if hasattr(view, "page") and view.page is not None:
            view.page.update()
    except Exception:
        pass  # view 已卸載，忽略


def run_ftb(view, *, dry_run: bool):
    """执行 FTB (Feed The Beast) 模组翻译流程"""
    in_dir = (view.ftb_in_dir.value or "").strip()
    if not in_dir:
        view._show_snack("請先選擇輸入資料夾", ft.Colors.RED_600)
        return
    if view.run_ftb_translation_service is None:
        view._show_snack("FTB service 尚未可用", ft.Colors.RED_600)
        return
    if view.TaskSession is None:
        view._show_snack("TaskSession 尚未可用", ft.Colors.RED_600)
        return
    out_dir = (view.ftb_out_dir.value or "").strip() or None
    view._set_status(
        "模擬執行" if dry_run else "執行中",
        ft.Colors.AMBER_200 if dry_run else ft.Colors.BLUE_200,
    )
    view.progress.value = 0
    view.log_view.controls.clear()
    _safe_page_update(view)
    view.session = view.TaskSession()
    try:
        view.session.start()
    except Exception as e:
        log_warning(f"FTB session.start() 失敗: {e}")

    def worker():
        """执行 FTB 翻译服务"""
        try:
            view.run_ftb_translation_service(
                in_dir,
                view.session,
                output_dir=out_dir,
                dry_run=dry_run,
                step_export=bool(view.ftb_step_export.value),
                step_clean=bool(view.ftb_step_clean.value),
                step_translate=bool(view.ftb_step_translate.value),
                step_inject=bool(view.ftb_step_inject.value),
                write_new_cache=bool(view.ftb_write_new_cache.value),
            )
        except Exception as ex:
            try:
                if hasattr(view.session, "add_log"):
                    _safe_add_log(view, f"[UI] 服務執行失敗：{ex}")
                if hasattr(view.session, "set_error"):
                    view.session.set_error(str(ex))
            except Exception as e:
                log_error(f"記錄 FTB 執行失敗時發生錯誤: {e}")

    threading.Thread(target=worker, daemon=True).start()
    view._start_ui_timer()


def run_kjs(view, *, dry_run: bool):
    """执行 KubeJS (KubeJavaScript) 工具提示翻译流程"""
    in_dir = (view.kjs_in_dir.value or "").strip()
    if not in_dir:
        view._show_snack("請先選擇輸入資料夾", ft.Colors.RED_600)
        return
    if view.run_kubejs_tooltip_service is None:
        view._show_snack("KubeJS service 尚未可用", ft.Colors.RED_600)
        return
    if view.TaskSession is None:
        view._show_snack("TaskSession 尚未可用", ft.Colors.RED_600)
        return
    out_dir = (view.kjs_out_dir.value or "").strip() or None
    view._set_status(
        "模擬執行" if dry_run else "執行中",
        ft.Colors.AMBER_200 if dry_run else ft.Colors.BLUE_200,
    )
    view.progress.value = 0
    view.log_view.controls.clear()
    _safe_page_update(view)
    view.session = view.TaskSession()
    try:
        view.session.start()
    except Exception as e:
        log_warning(f"FTB session.start() 失敗: {e}")

    def worker():
        """执行 KubeJS 翻译服务"""
        try:
            view.run_kubejs_tooltip_service(
                in_dir,
                view.session,
                output_dir=out_dir,
                dry_run=dry_run,
                step_extract=bool(view.kjs_step_extract.value),
                step_translate=bool(view.kjs_step_translate.value),
                step_inject=bool(view.kjs_step_inject.value),
                write_new_cache=bool(view.kjs_write_new_cache.value),
            )
        except Exception as ex:
            try:
                if hasattr(view.session, "add_log"):
                    _safe_add_log(view, f"[UI] 服務執行失敗：{ex}")
                if hasattr(view.session, "set_error"):
                    view.session.set_error(str(ex))
            except Exception as e:
                log_error(f"記錄 KubeJS 執行失敗時發生錯誤: {e}")

    threading.Thread(target=worker, daemon=True).start()
    view._start_ui_timer()


def run_md(view, *, dry_run: bool):
    """执行 Markdown 文档翻译流程"""
    in_dir = (view.md_in_dir.value or "").strip()
    if not in_dir:
        view._show_snack("請先選擇輸入資料夾", ft.Colors.RED_600)
        return
    if view.run_md_translation_service is None:
        view._show_snack("MD service 尚未可用", ft.Colors.RED_600)
        return
    if view.TaskSession is None:
        view._show_snack("TaskSession 尚未可用", ft.Colors.RED_600)
        return
    out_dir = (view.md_out_dir.value or "").strip() or None
    view._set_status(
        "模擬執行" if dry_run else "執行中",
        ft.Colors.AMBER_200 if dry_run else ft.Colors.BLUE_200,
    )
    view.progress.value = 0
    view.log_view.controls.clear()
    _safe_page_update(view)
    view.session = view.TaskSession()
    try:
        view.session.start()
    except Exception as e:
        log_warning(f"FTB session.start() 失敗: {e}")

    def worker():
        """执行 MD 翻译服务"""
        try:
            view.run_md_translation_service(
                input_dir=in_dir,
                session=view.session,
                output_dir=out_dir,
                dry_run=dry_run,
                step_extract=bool(view.md_step_extract.value),
                step_translate=bool(view.md_step_translate.value),
                step_inject=bool(view.md_step_inject.value),
                write_new_cache=bool(view.md_write_new_cache.value),
                lang_mode=str(view.md_lang_mode.value or "non_cjk_only"),
            )
        except Exception as ex:
            try:
                if hasattr(view.session, "add_log"):
                    _safe_add_log(view, f"[UI] 服務執行失敗：{ex}")
                if hasattr(view.session, "set_error"):
                    view.session.set_error(str(ex))
            except Exception as e:
                log_error(f"記錄 MD 執行失敗時發生錯誤: {e}")

    threading.Thread(target=worker, daemon=True).start()
    view._start_ui_timer()


def start_ui_timer(view):
    """启动 UI 定时器，定期从 TaskSession 读取状态更新翻译进度界面（方案B）。

    架構：背景執行緒只讀取 session.snapshot()，所有 UI 更新包裝為
    async closure 透過 page.run_task() 執行，確保執行緒安全。
    """
    if view._ui_timer_running:
        return
    view._ui_timer_running = True
    ui_cfg = load_ui_logging_config(load_config)
    presenter = LogPresenter(
        mode="tail",
        tail_lines=ui_cfg.get("tail_lines", 250),
        colorize=False,
        default_color=ft.Colors.GREY_100,
    )

    def loop():
        while view._ui_timer_running:
            time.sleep(0.1)
            if view.session is None:
                continue
            try:
                snap = view.session.snapshot()
            except Exception:
                continue

            progress = float(snap.get("progress", 0) or 0)
            logs = snap.get("logs", []) or []
            status = (snap.get("status") or "").upper()

            async def _do_update(_=None, p=progress, l=logs, s=status, pr=presenter):
                view.progress.value = p
                try:
                    pr.sync(view.log_view, l)
                    view.log_view.scroll_to(offset=1.0)
                except Exception as e:
                    log_warning(f"更新日誌視圖失敗: {e}")
                if s == "DONE":
                    view._set_status("任務完成", ft.Colors.GREEN_200)
                    view._ui_timer_running = False
                elif s == "ERROR":
                    view._set_status("任務發生錯誤", ft.Colors.RED_200)
                    view._ui_timer_running = False
                _safe_page_update(view)

            view.page.run_task(_do_update)

    threading.Thread(target=loop, daemon=True).start()
