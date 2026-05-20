from __future__ import annotations

import threading

from translation_tool.utils.log_unit import log_info, log_warning
from translation_tool.utils.config_manager import load_config
import time
from pathlib import Path
from typing import Any

import flet as ft

from app.logging import LogPresenter, LogEntry
from app.logging.task_session import TaskSession
from app.services_impl.logging_service import GLOBAL_LOG_LIMITER
from translation_tool.core.jar_processor import (
        extract_lang_files_generator,
        extract_book_files_generator,
        extract_dual_files_generator,
    )
from app.views.extractor.extractor_state import PreviewState

def update_stats_from_log(view, line: str, phase: str = None):
    """解析日誌行更新提取統計。

    規則：
    - 只在明確的「最終摘要」日誌上覆蓋 success / warnings / total_files
    - dual mode 時寫入對應 phase 的 sub-dict
    """
    stats = view._extraction_stats
    target = stats if phase is None else stats.get(phase, stats)
    try:
        import re

        final_match = re.search(
            r'已檢查\s+(\d+)/(\d+)\s+個\s+JAR\s+檔案。\s*\n\s*-\s*新提取或更新的檔案:\s*(\d+)\s+個\s*\n\s*-\s*因內容相同而跳過的檔案:\s*(\d+)\s+個',
            line,
            re.MULTILINE,
        )
        if final_match:
            target['success'] = int(final_match.group(1))
            target['warnings'] = int(final_match.group(4))
            target['failures'] = 0
            target['total_files'] = int(final_match.group(3))
            return

        error_match = re.search(r'提取\s+(.+?)\s+時產生例外', line)
        if error_match or '[ERROR]' in line or '[致命錯誤]' in line:
            target['failures'] = target.get('failures', 0) + 1
    except Exception as e:
        log_warning(f'解析統計數字失敗: {e}')

def start_ui_poller(view, mode: str = ''):
    """启动后台轮询线程，定期从 TaskSession 读取状态更新 UI"""
    view._ui_poller_stop.clear()
    view._extraction_stats = {'success': 0, 'warnings': 0, 'failures': 0, 'total_files': 0}
    presenter = LogPresenter(mode="append", max_ui_lines=2000)
    presenter.reset()

    def poll():
        """轮询 TaskSession 状态并更新 UI"""
        while not view._ui_poller_stop.is_set():
            snap = view.session.snapshot()
            status = snap['status']
            progress = snap['progress']
            logs = snap['logs']
            is_error = snap['error']

            if status == 'RUNNING':
                snap = view.session.snapshot()
                current = snap.get('current', 0)
                total = snap.get('total', 0)
                display_idx = current if current > 0 else 1
                view.status_text.value = f'狀態：提取 {mode} 中 ({display_idx}/{total}) ({int(progress * 100)}%)'
            elif status == 'DONE':
                view.status_text.value = '狀態：完成'
            elif status == 'ERROR':
                view.status_text.value = '狀態：發生錯誤'
            else:
                view.status_text.value = '狀態：閒置'

            view.progress_bar.value = progress
            view.progress_bar.color = ft.Colors.RED if is_error else ft.Colors.BLUE

            # LogPresenter 接管 append + truncate，回傳新增 entries
            # presenter.sync() 內部已直接 append 到 view.log_view.controls
            # caller 只做 stats side effect，不再重複渲染
            new_entries = presenter.sync(view.log_view, logs)
            for entry in new_entries:
                if entry.text.strip():
                    update_stats_from_log(view, entry.text)

            if status in ('DONE', 'ERROR'):
                view.set_controls_disabled(False)
                if status == 'DONE' and mode:
                    async def _do_show_summary(_):
                        view._show_extraction_summary(mode)
                    view.page.run_task(_do_show_summary, None)
                view.page.update()
                break

            view.page.update()
            time.sleep(0.1)

    threading.Thread(target=poll, daemon=True).start()

def _extraction_worker(view, mode: str, mods_dir: str, output_dir: str):
    """方案 2：Worker 直接更新 UI（廢除 poller），與 BundlerView 架構一致。

    每個 update：直接呼叫 view._append_log_line() + page.update()，
    不再透過 session.snapshot() + poller 中轉。
    """
    from app.services_impl.pipelines._pipeline_logging import ensure_pipeline_logging
    ensure_pipeline_logging()

    view._extraction_stats = {
        'success': 0,
        'warnings': 0,
        'failures': 0,
        'total_files': 0,
        'lang': {'success': 0, 'warnings': 0, 'failures': 0, 'total_files': 0},
        'book': {'success': 0, 'warnings': 0, 'failures': 0, 'total_files': 0},
    }
    session = view.session
    session.start()

    current_phase = "lang" if mode == "dual" else mode
    skip_zh_cn = getattr(view, 'skip_zh_cn_switch', None) and view.skip_zh_cn_switch.value
    if mode == 'lang':
        generator = extract_lang_files_generator(mods_dir, output_dir, skip_zh_cn=skip_zh_cn)
    elif mode == 'book':
        generator = extract_book_files_generator(mods_dir, output_dir)
    elif mode == 'dual':
        generator = extract_dual_files_generator(mods_dir, output_dir, skip_zh_cn=skip_zh_cn)

    try:
        lang_stats_done = False
        book_stats_done = False
        for update in generator:
            # phase 切換要在 filter 前處理（filter 會移除 phase 欄位）
            if "phase" in update:
                current_phase = update["phase"]
                if current_phase == "book":
                    async def _do_reset_progress(_):
                        view.progress_bar.value = 0.0
                    view.page.run_task(_do_reset_progress, None)

            filtered: dict[str, Any] | None = GLOBAL_LOG_LIMITER.filter(update)
            if filtered is None:
                continue

            log_msg = filtered.get("log", "")
            is_completion = '提取完成' in log_msg and '個 JAR' in log_msg
            if log_msg and not (mode == "dual" and is_completion):
                log_info(f"[DEBUG] _extraction_worker: received log_msg={log_msg[:80]}...")
                msg_for_ui = log_msg
                async def _do_append_log(_, m=msg_for_ui):
                    view._append_log_line(m)
                view.page.run_task(_do_append_log, None)
                if mode != "dual":
                    update_stats_from_log(view, log_msg, phase=current_phase)

            if "progress" in filtered or "progress" in update:
                progress = filtered.get("progress", update.get("progress", 0))
                current = update.get("current", 0)
                total = update.get("total", 0)
                display_idx = current if current > 0 else 1
                this_phase = current_phase
                phase_label = f"[{this_phase.upper()}]" if mode == "dual" else ""
                status_text = f'狀態：提取 {mode.upper()} {phase_label} 中 ({display_idx}/{total}) ({int(progress * 100)}%)'

                async def _do_update(_, p=progress, s=status_text):
                    view.status_text.value = s
                    view.progress_bar.value = p
                    view.page.update()

                view.page.run_task(_do_update, None)

            if "stats" in update:
                stats = update["stats"]
                async def _do_update_stats(_, s=stats):
                    view._extraction_stats = s
                view.page.run_task(_do_update_stats, None)
                if current_phase == "lang":
                    lang_stats_done = True
                elif current_phase == "book":
                    book_stats_done = True

            is_error = update.get("error", False)
            if is_error:
                async def _do_error(_):
                    view.progress_bar.color = ft.Colors.RED
                    view.page.update()
                view.page.run_task(_do_error, None)
                session.set_error()
                break

        final: dict[str, Any] | None = GLOBAL_LOG_LIMITER.flush()
        if final and "log" in final:
            async def _do_append_final(_):
                view._append_log_line(final["log"])
            view.page.run_task(_do_append_final, None)

    except Exception as e:
        import traceback
        async def _do_error_log(_):
            view._append_log_line(f"[ERROR] {e}")
            view._append_log_line(traceback.format_exc())
        view.page.run_task(_do_error_log, None)
        session.set_error()
    finally:
        if not session.error:
            session.finish()

        status = session.snapshot()['status']

        if status == 'DONE':
            view.status_text.value = '狀態：完成'
            view.progress_bar.value = 1.0

            async def _do_show_summary(_):
                view._show_extraction_summary(mode)

            view.page.run_task(_do_show_summary, None)
        elif status == 'ERROR':
            view.status_text.value = '狀態：發生錯誤'
            view.progress_bar.color = ft.Colors.RED

        view.set_controls_disabled(False)
        view.page.update()


def start_extraction(view, mode: str):
    """启动 JAR 文件提取任务，根据 mode 选择 lang 或 book 提取服务"""
    snap = view.session.snapshot()
    if snap.get('status') == 'RUNNING':
        view._show_snack_bar('任務進行中...')
        return

    mods_dir = (view.mods_dir_textfield.value or '').strip()

    if not mods_dir:
        view._show_snack_bar('請先選擇 Mods 資料夾')
        return

    mods_path = Path(mods_dir)
    if not mods_path.exists():
        view._show_snack_bar('Mods 資料夾不存在')
        return

    view._auto_fill_output_path(mods_dir, mode)
    output_dir = (view.output_dir_textfield.value or '').strip()
    out_path = Path(output_dir)
    try:
        out_path.mkdir(parents=True, exist_ok=True)
    except Exception as ex:
        view._show_snack_bar('無法建立輸出資料夾')
        view._append_log_line(f'[ERROR] {ex}')
        return

    view.set_controls_disabled(True)
    view.log_view.controls.clear()
    view.session.start()
    view._append_log_line(f'[系統] 開始任務 ({mode})...')
    view.progress_bar.value = 0
    view.progress_bar.color = ft.Colors.BLUE

    threading.Thread(target=_extraction_worker, args=(view, mode, mods_dir, str(out_path)), daemon=True).start()

def build_preview_result_dialog(view, result: dict, mode: str):
    """构建提取预览结果对话框，显示找到的文件数量和大小"""
    preview_results = result.get('preview_results', [])
    total_files = result.get('total_files', 0)
    total_size_mb = result.get('total_size_mb', 0)
    output_dir = (view.output_dir_textfield.value or '').strip()
    has_report = output_dir and Path(output_dir).exists()

    controls = [
        ft.Text(f'預覽結果（{mode.upper()}）', size=16, weight=ft.FontWeight.BOLD),
        ft.Divider(),
    ]

    # dual mode 分開顯示 Lang/Book 數量，其他模式顯示總檔案數
    if mode == "dual":
        total_lang = sum(r.get('lang_count', 0) for r in preview_results)
        total_book = sum(r.get('book_count', 0) for r in preview_results)
        controls.append(ft.Text(f'Lang：{total_lang} 個', size=14, color=ft.Colors.BLUE_700))
        controls.append(ft.Text(f'Book：{total_book} 個', size=14, color=ft.Colors.BLUE_700))
    else:
        controls.append(ft.Text(f'共找到 {total_files} 個檔案', size=14, color=ft.Colors.BLUE_700))

    controls.extend([
        ft.Text(f'總大小：{total_size_mb:.2f} MB', size=14, color=ft.Colors.BLUE_700),
    ])

    if has_report:
        try:
            import glob
            pattern = str(Path(output_dir) / f'preview_report_{mode}_*.md')
            report_files = glob.glob(pattern)
            report_name = Path(max(report_files, key=lambda p: Path(p).stat().st_mtime)).name if report_files else '(找不到報告檔案)'
        except Exception:
            report_name = f'preview_report_{mode}_*.md'
        controls.append(
            ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.DESCRIPTION, size=16, color=ft.Colors.GREEN_700),
                        ft.Text('詳細報告已生成到輸出資料夾', size=12, color=ft.Colors.GREEN_700, weight=ft.FontWeight.BOLD),
                    ], spacing=4),
                    ft.Text(f'📄 {report_name}', size=11, color=ft.Colors.GREEN_900, selectable=True),
                ], spacing=4, tight=True),
                padding=8,
                bgcolor=ft.Colors.GREEN_50,
                border_radius=8,
            )
        )

    controls.extend([ft.Divider(), ft.Text(f'詳細清單（{len(preview_results)} 個 JAR）：', size=13, weight=ft.FontWeight.BOLD)])
    jar_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)
    for r in preview_results:
        if mode == "dual":
            total_count = r.get('lang_count', 0) + r.get('book_count', 0)
            jar_list.controls.append(ft.Text(f"📦 {r['jar']}: Lang {r.get('lang_count', 0)} 個 / Book {r.get('book_count', 0)} 個", size=12))
        else:
            jar_list.controls.append(ft.Text(f"📦 {r['jar']}: {r['count']} 個檔案 ({r['size_mb']:.1f} MB)", size=12))
    list_container = ft.Container(content=jar_list, height=300, padding=5, bgcolor=ft.Colors.GREY_100, border_radius=8)
    controls.append(list_container)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(f'提取預覽 - {mode.upper()}'),
        content=ft.Container(content=ft.Column(controls, spacing=8, scroll=ft.ScrollMode.AUTO), width=600, height=500),
        actions=[
            ft.TextButton('取消', on_click=lambda e: view._close_dialog_overlay(dialog)),
            ft.Button('確認提取', icon=ft.Icons.CHECK, on_click=lambda e: view._start_from_preview_overlay(dialog, mode)),
        ],
    )
    return dialog

def build_preview_error_dialog(view, error: str, mode: str):
    """构建预览失败错误对话框"""
    return ft.AlertDialog(
        modal=True,
        title=ft.Text('預覽失敗'),
        content=ft.Text(f'無法預覽 {mode.upper()} 提取：{error}'),
        actions=[ft.TextButton('關閉', on_click=lambda e: view._close_dialog_overlay(view._preview_error_dialog))],
    )

def show_preview(view, mode: str):
    """执行预览扫描，显示将要提取的文件列表（不执行实际提取）"""
    mods_dir = (view.mods_dir_textfield.value or '').strip()
    if not mods_dir:
        view._show_snack_bar('請先選擇 Mods 資料夾')
        return

    mods_path = Path(mods_dir)
    if not mods_path.exists():
        view._show_snack_bar('Mods 資料夾不存在')
        return

    view._auto_fill_output_path(mods_dir, mode)
    view._show_snack_bar(f'正在掃描 {mode.upper()} 檔案...', ft.Colors.BLUE_600)
    view._append_log_line('[系統] 開始預覽掃描...')
    view.set_controls_disabled(True)
    from translation_tool.core.jar_processor import preview_extraction_generator, find_jar_files
    jar_files = find_jar_files(mods_dir)
    total_jars = len(jar_files)

    preview_state = PreviewState()
    preview_state.total = total_jars
    preview_state.current = 0

    def do_preview():
        """执行预览扫描生成器，收集提取结果"""
        try:
            for update in preview_extraction_generator(mods_dir, mode):
                if 'error' in update:
                    preview_state.error = update['error']
                    preview_state.done = True
                    break
                preview_state.progress = update.get('progress', 0)
                preview_state.current = update.get('current', 0)
                preview_state.total = update.get('total', 0)
                if 'result' in update:
                    preview_state.result = update['result']
                    preview_state.done = True
        except Exception as ex:
            preview_state.error = str(ex)
            preview_state.done = True

    threading.Thread(target=do_preview, daemon=True).start()

    def poll():
        """轮询预览状态并更新 UI"""
        while not preview_state.done:
            progress = preview_state.progress
            current = preview_state.current
            total = preview_state.total
            done = preview_state.done

            async def _do_update(_):
                view.progress_bar.value = progress
                view.progress_bar.color = ft.Colors.BLUE
                if total > 0:
                    display_idx = current if current > 0 else 1
                    view.status_text.value = f'狀態：預覽掃描中 ({display_idx}/{total}) ({int(progress * 100)}%)'
                else:
                    view.status_text.value = '狀態：預覽掃描中...'
                view.page.update()

            view.page.run_task(_do_update, None)
            time.sleep(0.1)

        view.set_controls_disabled(False)
        view.status_text.value = '狀態：預覽完成'
        view.progress_bar.value = 1.0
        log_final = f"[系統] 預覽完成：error={preview_state.error is not None}, result={preview_state.result is not None}"
        log_info(f"[DEBUG] poll: calling _append_log_line: {log_final[:80]}...")
        view._append_log_line(log_final)
        try:
            view.page.update()
        except Exception as e:
            log_warning(f'更新預覽完成 UI 失敗: {e}')

        if preview_state.error:
            view._append_log_line(f'[ERROR] 預覽錯誤：{preview_state.error}')
            view._show_preview_dialog_error(preview_state.error, mode)
        elif preview_state.result:
            result = preview_state.result
            view._append_log_line(f"[系統] 找到 {result.get('total_files', 0)} 個檔案，準備顯示預覽對話框")
            output_dir = (view.output_dir_textfield.value or '').strip()

            if output_dir:
                output_path = Path(output_dir)
            else:
                mods_path = Path((view.mods_dir_textfield.value or '').strip())
                config = load_config()
                folder_names = config.get("extractor", {}).get("output_folder_names", {})
                lang_preview = folder_names.get("lang_preview", "_預覽lang_輸出")
                book_preview = folder_names.get("book_preview", "_預覽book_輸出")
                suffix = lang_preview if mode == 'lang' else book_preview
                output_path = mods_path.with_name(mods_path.name + suffix) if mods_path.exists() else None
                if output_path:
                    output_dir = str(output_path)
                    view._append_log_line(f'[系統] 自動設定輸出路徑：{output_dir}')

            if output_dir:
                try:
                    from translation_tool.core.jar_processor import generate_preview_report
                    output_path = Path(output_dir)
                    if not output_path.exists():
                        output_path.mkdir(parents=True, exist_ok=True)
                        view._append_log_line('[系統] ✅ 資料夾建立成功')
                    report_path = generate_preview_report(result, mode, output_dir)
                    view._append_log_line('[系統] ✅ 預覽報告已成功輸出')
                    view._append_log_line(f'[系統] 📄 報告路徑：{report_path}')
                    view._show_snack_bar('預覽報告已生成', ft.Colors.GREEN_600)
                except Exception as ex:
                    import traceback
                    view._append_log_line(f'[ERROR] ❌ 生成預覽報告失敗：{ex}')
                    view._append_log_line(f'[ERROR] {traceback.format_exc()}')
            else:
                view._append_log_line('[系統] ⚠️ 未設定輸出路徑，跳過報告生成')
            view._show_preview_dialog_result(result, mode)
        else:
            view._append_log_line('[WARN] 預覽無結果')
            view._show_snack_bar('預覽無結果', ft.Colors.ORANGE_400)

    threading.Thread(target=poll, daemon=True).start()
