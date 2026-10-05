from __future__ import annotations

import asyncio
import functools
import threading
import traceback

import flet as ft  # noqa: F401

from app.tasks.task_session import tag_session
from app.ui.design import C
from app.ui.snack import show_snack
from translation_tool.utils.log_unit import log_error, log_warning


# =========================================================
# 執行緒安全的 UI 更新包裝函式（ATK-004 / ATK-017 修復）
# =========================================================
def _safe_add_log(view, message: str, level: str = "info", *, mirror: bool = True):
    """執行緒安全地寫入 session log。view 已卸載時靜靜忽略。

    ``mirror=False``：呼叫端已自行把更完整的內容寫進後台時使用，避免後台重複。
    """
    try:
        if hasattr(view, "session") and view.session is not None:
            try:
                view.session.add_log(message, level=level, mirror=mirror)
            except TypeError:  # 舊版 / 替身 session 沒有 mirror 或 level 參數
                view.session.add_log(message)
    except Exception:  # noqa: BLE001, S110
        pass  # view 已卸載或 session 已 GC，忽略


def _log_task_start(view, label: str, in_dir: str, out_dir, dry_run: bool, **steps):
    """在任務日誌開頭寫下輸入、輸出與步驟設定，排查時才知道這次是用什麼參數跑的。"""
    enabled = "、".join(name for name, on in steps.items() if on) or "（無）"
    _safe_add_log(
        view,
        f"[系統] 開始 {label}：{'模擬執行（不送出 API）' if dry_run else '正式執行'}"
        f"｜輸入：{in_dir}｜輸出：{out_dir or '（預設位置）'}｜步驟：{enabled}",
    )


def _report_service_failure(view, label: str, ex: Exception, in_dir: str) -> None:
    """服務拋出未預期例外：完整堆疊寫後台，畫面顯示例外類型、訊息與去向。"""
    log_error(
        f"[UI] {label} 服務執行失敗（輸入：{in_dir}）：{ex!r}\n{traceback.format_exc()}"
    )
    _safe_add_log(
        view,
        f"[UI] {label} 服務執行失敗：{type(ex).__name__}: {ex}"
        "（完整堆疊已寫入後台 log）",
        "error",
        mirror=False,
    )


def _safe_page_update(view):
    """執行緒安全地更新 page。view 已卸載時靜靜忽略。"""
    try:
        if hasattr(view, "page") and view.page is not None:
            view.page.update()
    except Exception:  # noqa: BLE001, S110
        pass  # view 已卸載，忽略


def _is_running(view) -> bool:
    """同一頁同時只允許一個翻譯任務（避免重複送出 API、同時寫入同一輸出/快取）。"""
    return bool(getattr(view, "_ui_timer_running", False))


def run_ftb(view, *, dry_run: bool):
    """执行 FTB (Feed The Beast) 模组翻译流程"""
    if _is_running(view):
        show_snack(view.page, "已有翻譯任務執行中，請等待完成", C.GOLD)
        return
    in_dir = (view.ftb_in_dir.value or "").strip()
    if not in_dir:
        show_snack(view.page, "請先選擇輸入資料夾", C.RED)
        return
    if view.run_ftb_translation_service is None:
        show_snack(view.page, "FTB service 尚未可用", C.RED)
        return
    if view.TaskSession is None:
        show_snack(view.page, "TaskSession 尚未可用", C.RED)
        return
    out_dir = (view.ftb_out_dir.value or "").strip() or None
    view._set_status(
        "模擬執行" if dry_run else "執行中",
        C.GOLD_BG if dry_run else C.DIA_BG,
    )
    view.progress.value = 0
    view.log_view.clear()
    _safe_page_update(view)
    view.session = tag_session(view.TaskSession(), "FTB 任務翻譯", "translation")
    try:
        view.session.start()
    except Exception as e:  # noqa: BLE001
        log_warning(f"FTB session.start() 失敗: {e!r}", exc_info=True)
    _log_task_start(
        view,
        "FTB 任務翻譯",
        in_dir,
        out_dir,
        dry_run,
        匯出=view.ftb_step_export.value,
        清理=view.ftb_step_clean.value,
        翻譯=view.ftb_step_translate.value,
        注入=view.ftb_step_inject.value,
        寫入新快取=view.ftb_write_new_cache.value,
    )

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
        except Exception as ex:  # noqa: BLE001
            try:
                if hasattr(view.session, "add_log"):
                    _report_service_failure(view, "FTB 任務翻譯", ex, in_dir)
                if hasattr(view.session, "set_error"):
                    view.session.set_error()
                if hasattr(view.session, "finish"):
                    view.session.finish()  # set_error() → finish()：TaskManager 才會離開 active
            except Exception as e:  # noqa: BLE001
                log_error(f"記錄 FTB 執行失敗時發生錯誤: {e!r}", exc_info=True)

    threading.Thread(target=worker, daemon=True).start()
    view._start_ui_timer()


def run_kjs(view, *, dry_run: bool):
    """执行 KubeJS (KubeJavaScript) 工具提示翻译流程"""
    if _is_running(view):
        show_snack(view.page, "已有翻譯任務執行中，請等待完成", C.GOLD)
        return
    in_dir = (view.kjs_in_dir.value or "").strip()
    if not in_dir:
        show_snack(view.page, "請先選擇輸入資料夾", C.RED)
        return
    if view.run_kubejs_tooltip_service is None:
        show_snack(view.page, "KubeJS service 尚未可用", C.RED)
        return
    if view.TaskSession is None:
        show_snack(view.page, "TaskSession 尚未可用", C.RED)
        return
    out_dir = (view.kjs_out_dir.value or "").strip() or None
    view._set_status(
        "模擬執行" if dry_run else "執行中",
        C.GOLD_BG if dry_run else C.DIA_BG,
    )
    view.progress.value = 0
    view.log_view.clear()
    _safe_page_update(view)
    view.session = tag_session(view.TaskSession(), "KubeJS 任務翻譯", "translation")
    try:
        view.session.start()
    except Exception as e:  # noqa: BLE001
        log_warning(f"KubeJS session.start() 失敗: {e!r}", exc_info=True)
    _log_task_start(
        view,
        "KubeJS 任務翻譯",
        in_dir,
        out_dir,
        dry_run,
        提取=view.kjs_step_extract.value,
        翻譯=view.kjs_step_translate.value,
        注入=view.kjs_step_inject.value,
        寫入新快取=view.kjs_write_new_cache.value,
    )

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
        except Exception as ex:  # noqa: BLE001
            try:
                if hasattr(view.session, "add_log"):
                    _report_service_failure(view, "KubeJS 任務翻譯", ex, in_dir)
                if hasattr(view.session, "set_error"):
                    view.session.set_error()
                if hasattr(view.session, "finish"):
                    view.session.finish()  # set_error() → finish()：TaskManager 才會離開 active
            except Exception as e:  # noqa: BLE001
                log_error(f"記錄 KubeJS 執行失敗時發生錯誤: {e!r}", exc_info=True)

    threading.Thread(target=worker, daemon=True).start()
    view._start_ui_timer()


def run_md(view, *, dry_run: bool):
    """执行 Markdown 文档翻译流程"""
    if _is_running(view):
        show_snack(view.page, "已有翻譯任務執行中，請等待完成", C.GOLD)
        return
    in_dir = (view.md_in_dir.value or "").strip()
    if not in_dir:
        show_snack(view.page, "請先選擇輸入資料夾", C.RED)
        return
    if view.run_md_translation_service is None:
        show_snack(view.page, "MD service 尚未可用", C.RED)
        return
    if view.TaskSession is None:
        show_snack(view.page, "TaskSession 尚未可用", C.RED)
        return
    out_dir = (view.md_out_dir.value or "").strip() or None
    view._set_status(
        "模擬執行" if dry_run else "執行中",
        C.GOLD_BG if dry_run else C.DIA_BG,
    )
    view.progress.value = 0
    view.log_view.clear()
    _safe_page_update(view)
    view.session = tag_session(view.TaskSession(), "MD 任務翻譯", "translation")
    try:
        view.session.start()
    except Exception as e:  # noqa: BLE001
        log_warning(f"MD session.start() 失敗: {e!r}", exc_info=True)
    _log_task_start(
        view,
        "MD 任務翻譯",
        in_dir,
        out_dir,
        dry_run,
        提取=view.md_step_extract.value,
        翻譯=view.md_step_translate.value,
        注入=view.md_step_inject.value,
        寫入新快取=view.md_write_new_cache.value,
    )

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
        except Exception as ex:  # noqa: BLE001
            try:
                if hasattr(view.session, "add_log"):
                    _report_service_failure(view, "MD 任務翻譯", ex, in_dir)
                if hasattr(view.session, "set_error"):
                    view.session.set_error()
                if hasattr(view.session, "finish"):
                    view.session.finish()  # set_error() → finish()：TaskManager 才會離開 active
            except Exception as e:  # noqa: BLE001
                log_error(f"記錄 MD 執行失敗時發生錯誤: {e!r}", exc_info=True)

    threading.Thread(target=worker, daemon=True).start()
    view._start_ui_timer()


_POLL_INTERVAL_SEC = 0.2


def start_ui_timer(view):
    """啟動 UI 輪詢，定期把 TaskSession 的進度與日誌同步到畫面。

    輪詢在 Flet event loop 上執行（page.run_task），背景執行緒不直接更新 UI。
    """
    view._ui_timer_running = True
    if view._poller.running:
        return
    view._poller.start(view.page, functools.partial(_poll_session, view))


def stop_ui_timer(view):
    """停止輪詢（換頁／卸載）。背景任務照常執行；``_ui_timer_running`` 保留，
    重新掛載時由 ``resume_ui_timer`` 接續（任務若已結束會補上最終狀態）。"""
    view._poller.stop()


def resume_ui_timer(view):
    """重新掛載後：任務仍在追蹤中就重新啟動輪詢（內部會先同步一次最新狀態）。"""
    if view._ui_timer_running and view.session is not None:
        view._poller.start(view.page, functools.partial(_poll_session, view))


async def _poll_session(view, alive=lambda: True):
    """定期同步 session 狀態，直到任務結束、頁面已關閉或輪詢被停止（unmount）。"""
    while alive() and view._ui_timer_running:
        try:
            _sync_from_session(view)
        except RuntimeError as e:
            log_warning(f"翻譯頁 UI 輪詢停止：{e}")
            view._ui_timer_running = False
            break
        if alive() and view._ui_timer_running:
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
            view._set_status("任務發生錯誤", C.RED_BG)
        elif getattr(view.session, "cancel_requested", False):
            view._set_status("已取消", C.GOLD_BG)
        else:
            view._set_status("任務完成", C.EM_BG)
        view._ui_timer_running = False
        cancel_button = getattr(view, "cancel_button", None)
        if cancel_button is not None:
            cancel_button.disabled = True
    view.page.update()
