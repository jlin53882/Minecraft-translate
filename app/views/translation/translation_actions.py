from __future__ import annotations

import asyncio
import threading

import flet as ft

from app.ui.snack import show_snack
from translation_tool.utils.log_unit import log_error, log_warning


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


def _is_running(view) -> bool:
    """同一頁同時只允許一個翻譯任務（避免重複送出 API、同時寫入同一輸出/快取）。"""
    return bool(getattr(view, "_ui_timer_running", False))


def run_ftb(view, *, dry_run: bool):
    """执行 FTB (Feed The Beast) 模组翻译流程"""
    if _is_running(view):
        show_snack(view.page, "已有翻譯任務執行中，請等待完成", ft.Colors.AMBER_700)
        return
    in_dir = (view.ftb_in_dir.value or "").strip()
    if not in_dir:
        show_snack(view.page, "請先選擇輸入資料夾", ft.Colors.RED_600)
        return
    if view.run_ftb_translation_service is None:
        show_snack(view.page, "FTB service 尚未可用", ft.Colors.RED_600)
        return
    if view.TaskSession is None:
        show_snack(view.page, "TaskSession 尚未可用", ft.Colors.RED_600)
        return
    out_dir = (view.ftb_out_dir.value or "").strip() or None
    view._set_status(
        "模擬執行" if dry_run else "執行中",
        ft.Colors.AMBER_200 if dry_run else ft.Colors.BLUE_200,
    )
    view.progress.value = 0
    view.log_view.clear()
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
                    view.session.set_error()
            except Exception as e:
                log_error(f"記錄 FTB 執行失敗時發生錯誤: {e}")

    threading.Thread(target=worker, daemon=True).start()
    view._start_ui_timer()


def run_kjs(view, *, dry_run: bool):
    """执行 KubeJS (KubeJavaScript) 工具提示翻译流程"""
    if _is_running(view):
        show_snack(view.page, "已有翻譯任務執行中，請等待完成", ft.Colors.AMBER_700)
        return
    in_dir = (view.kjs_in_dir.value or "").strip()
    if not in_dir:
        show_snack(view.page, "請先選擇輸入資料夾", ft.Colors.RED_600)
        return
    if view.run_kubejs_tooltip_service is None:
        show_snack(view.page, "KubeJS service 尚未可用", ft.Colors.RED_600)
        return
    if view.TaskSession is None:
        show_snack(view.page, "TaskSession 尚未可用", ft.Colors.RED_600)
        return
    out_dir = (view.kjs_out_dir.value or "").strip() or None
    view._set_status(
        "模擬執行" if dry_run else "執行中",
        ft.Colors.AMBER_200 if dry_run else ft.Colors.BLUE_200,
    )
    view.progress.value = 0
    view.log_view.clear()
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
                    view.session.set_error()
            except Exception as e:
                log_error(f"記錄 KubeJS 執行失敗時發生錯誤: {e}")

    threading.Thread(target=worker, daemon=True).start()
    view._start_ui_timer()


def run_md(view, *, dry_run: bool):
    """执行 Markdown 文档翻译流程"""
    if _is_running(view):
        show_snack(view.page, "已有翻譯任務執行中，請等待完成", ft.Colors.AMBER_700)
        return
    in_dir = (view.md_in_dir.value or "").strip()
    if not in_dir:
        show_snack(view.page, "請先選擇輸入資料夾", ft.Colors.RED_600)
        return
    if view.run_md_translation_service is None:
        show_snack(view.page, "MD service 尚未可用", ft.Colors.RED_600)
        return
    if view.TaskSession is None:
        show_snack(view.page, "TaskSession 尚未可用", ft.Colors.RED_600)
        return
    out_dir = (view.md_out_dir.value or "").strip() or None
    view._set_status(
        "模擬執行" if dry_run else "執行中",
        ft.Colors.AMBER_200 if dry_run else ft.Colors.BLUE_200,
    )
    view.progress.value = 0
    view.log_view.clear()
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
                    view.session.set_error()
            except Exception as e:
                log_error(f"記錄 MD 執行失敗時發生錯誤: {e}")

    threading.Thread(target=worker, daemon=True).start()
    view._start_ui_timer()


_POLL_INTERVAL_SEC = 0.2


def start_ui_timer(view):
    """啟動 UI 輪詢，定期把 TaskSession 的進度與日誌同步到畫面。

    輪詢在 Flet event loop 上執行（page.run_task），背景執行緒不直接更新 UI。
    """
    if view._ui_timer_running:
        return
    view._ui_timer_running = True
    view.page.run_task(_poll_session, view)


async def _poll_session(view):
    """定期同步 session 狀態，直到任務結束或頁面已關閉。"""
    while view._ui_timer_running:
        try:
            _sync_from_session(view)
        except RuntimeError as e:
            log_warning(f"翻譯頁 UI 輪詢停止：{e}")
            view._ui_timer_running = False
            break
        if view._ui_timer_running:
            await asyncio.sleep(_POLL_INTERVAL_SEC)


def _sync_from_session(view):
    """同步一次進度/日誌/狀態。"""
    if view.session is None:
        return
    snap = view.session.snapshot()
    try:
        view.progress.value = float(snap.get("progress", 0) or 0)
    except (TypeError, ValueError):
        view.progress.value = 0
    # LogView 內建 auto_scroll；畫面由下方 page.update() 一次刷新
    view.log_view.sync_entries(snap.get("logs", []) or [], update=False)
    status = (snap.get("status") or "").upper()
    if status in ("DONE", "ERROR"):
        if status == "ERROR":
            view._set_status("任務發生錯誤", ft.Colors.RED_200)
        elif getattr(view.session, "cancel_requested", False):
            view._set_status("已取消", ft.Colors.AMBER_200)
        else:
            view._set_status("任務完成", ft.Colors.GREEN_200)
        view._ui_timer_running = False
        cancel_button = getattr(view, "cancel_button", None)
        if cancel_button is not None:
            cancel_button.disabled = True
    view.page.update()
