from __future__ import annotations

import threading
import time

import flet as ft

def run_ftb(view, *, dry_run: bool):
    """执行 FTB (Feed The Beast) 模组翻译流程"""
    in_dir = (view.ftb_in_dir.value or '').strip()
    if not in_dir:
        view._show_snack('請先選擇輸入資料夾', ft.Colors.RED_600)
        return
    if view.run_ftb_translation_service is None:
        view._show_snack('FTB service 尚未可用', ft.Colors.RED_600)
        return
    if view.TaskSession is None:
        view._show_snack('TaskSession 尚未可用', ft.Colors.RED_600)
        return
    out_dir = (view.ftb_out_dir.value or '').strip() or None
    view._set_status('模擬執行' if dry_run else '執行中', ft.Colors.AMBER_200 if dry_run else ft.Colors.BLUE_200)
    view.progress.value = 0
    view.log_view.controls.clear()
    view.page.update()
    view.session = view.TaskSession()
    try:
        view.session.start()
    except Exception:
        pass
    def worker():
        """执行 FTB 翻译服务"""
        try:
            view.run_ftb_translation_service(in_dir, view.session, output_dir=out_dir, dry_run=dry_run, step_export=bool(view.ftb_step_export.value), step_clean=bool(view.ftb_step_clean.value), step_translate=bool(view.ftb_step_translate.value), step_inject=bool(view.ftb_step_inject.value), write_new_cache=bool(view.ftb_write_new_cache.value))
        except Exception as ex:
            try:
                if hasattr(view.session, 'add_log'):
                    view.session.add_log(f'[UI] 服務執行失敗：{ex}')
                if hasattr(view.session, 'set_error'):
                    view.session.set_error(str(ex))
            except Exception:
                pass
    threading.Thread(target=worker, daemon=True).start()
    view._start_ui_timer()

def run_kjs(view, *, dry_run: bool):
    """执行 KubeJS (KubeJavaScript) 工具提示翻译流程"""
    in_dir = (view.kjs_in_dir.value or '').strip()
    if not in_dir:
        view._show_snack('請先選擇輸入資料夾', ft.Colors.RED_600)
        return
    if view.run_kubejs_tooltip_service is None:
        view._show_snack('KubeJS service 尚未可用', ft.Colors.RED_600)
        return
    if view.TaskSession is None:
        view._show_snack('TaskSession 尚未可用', ft.Colors.RED_600)
        return
    out_dir = (view.kjs_out_dir.value or '').strip() or None
    view._set_status('模擬執行' if dry_run else '執行中', ft.Colors.AMBER_200 if dry_run else ft.Colors.BLUE_200)
    view.progress.value = 0
    view.log_view.controls.clear()
    view.page.update()
    view.session = view.TaskSession()
    try:
        view.session.start()
    except Exception:
        pass
    def worker():
        """执行 KubeJS 翻译服务"""
        try:
            view.run_kubejs_tooltip_service(in_dir, view.session, output_dir=out_dir, dry_run=dry_run, step_extract=bool(view.kjs_step_extract.value), step_translate=bool(view.kjs_step_translate.value), step_inject=bool(view.kjs_step_inject.value), write_new_cache=bool(view.kjs_write_new_cache.value))
        except Exception as ex:
            try:
                if hasattr(view.session, 'add_log'):
                    view.session.add_log(f'[UI] 服務執行失敗：{ex}')
                if hasattr(view.session, 'set_error'):
                    view.session.set_error(str(ex))
            except Exception:
                pass
    threading.Thread(target=worker, daemon=True).start()
    view._start_ui_timer()

def run_md(view, *, dry_run: bool):
    """执行 Markdown 文档翻译流程"""
    in_dir = (view.md_in_dir.value or '').strip()
    if not in_dir:
        view._show_snack('請先選擇輸入資料夾', ft.Colors.RED_600)
        return
    if view.run_md_translation_service is None:
        view._show_snack('MD service 尚未可用', ft.Colors.RED_600)
        return
    if view.TaskSession is None:
        view._show_snack('TaskSession 尚未可用', ft.Colors.RED_600)
        return
    out_dir = (view.md_out_dir.value or '').strip() or None
    view._set_status('模擬執行' if dry_run else '執行中', ft.Colors.AMBER_200 if dry_run else ft.Colors.BLUE_200)
    view.progress.value = 0
    view.log_view.controls.clear()
    view.page.update()
    view.session = view.TaskSession()
    try:
        view.session.start()
    except Exception:
        pass
    def worker():
        """执行 MD 翻译服务"""
        try:
            view.run_md_translation_service(input_dir=in_dir, session=view.session, output_dir=out_dir, dry_run=dry_run, step_extract=bool(view.md_step_extract.value), step_translate=bool(view.md_step_translate.value), step_inject=bool(view.md_step_inject.value), write_new_cache=bool(view.md_write_new_cache.value), lang_mode=str(view.md_lang_mode.value or 'non_cjk_only'))
        except Exception as ex:
            try:
                if hasattr(view.session, 'add_log'):
                    view.session.add_log(f'[UI] 服務執行失敗：{ex}')
                if hasattr(view.session, 'set_error'):
                    view.session.set_error(str(ex))
            except Exception:
                pass
    threading.Thread(target=worker, daemon=True).start()
    view._start_ui_timer()

def start_ui_timer(view):
    """启动 UI 定时器，定期从 TaskSession 读取状态更新翻译进度界面"""
    if view._ui_timer_running:
        return
    view._ui_timer_running = True
    def loop():
        """定时轮询 session 状态并更新 UI"""
        while view._ui_timer_running:
            time.sleep(0.1)
            if view.session is None:
                continue
            try:
                snap = view.session.snapshot()
            except Exception:
                continue
            try:
                view.progress.value = float(snap.get('progress', 0) or 0)
            except Exception:
                view.progress.value = 0
            logs = snap.get('logs', []) or []
            try:
                tail = logs[-250:]
                view.log_view.controls = [ft.Text(line, size=13, color=ft.Colors.GREY_100) for line in tail]
            except Exception:
                pass
            status = (snap.get('status') or '').upper()
            if status == 'DONE':
                view._set_status('任務完成', ft.Colors.GREEN_200)
                view._ui_timer_running = False
            elif status == 'ERROR':
                view._set_status('任務發生錯誤', ft.Colors.RED_200)
                view._ui_timer_running = False
            view.page.update()
    threading.Thread(target=loop, daemon=True).start()
