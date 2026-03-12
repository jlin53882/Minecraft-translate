from __future__ import annotations

import threading
import time
from pathlib import Path

import flet as ft

from app.services_impl.pipelines.extract_service import (
    run_book_extraction_service,
    run_lang_extraction_service,
)
from app.views.extractor.extractor_state import PreviewState


def update_stats_from_log(view, line: str):
    stats = view._extraction_stats
    if '成功提取' in line and '個新檔案' in line:
        try:
            import re
            match = re.search(r'成功提取 (\d+) 個新檔案', line)
            if match:
                count = int(match.group(1))
                stats['success'] += 1
                stats['total_files'] += count
        except Exception:
            pass
    elif '跳過' in line or '已存在' in line:
        stats['warnings'] += 1
    elif '[ERROR]' in line or '失敗' in line or '錯誤' in line:
        stats['failures'] += 1


def start_ui_poller(view, mode: str = ''):
    view._ui_poller_stop.clear()
    view._last_rendered_log_count = 0
    view._extraction_stats = {'success': 0, 'warnings': 0, 'failures': 0, 'total_files': 0}

    def poll():
        while not view._ui_poller_stop.is_set():
            snap = view.session.snapshot()
            status = snap['status']
            progress = snap['progress']
            logs = snap['logs']
            is_error = snap['error']

            if status == 'RUNNING':
                view.status_text.value = '狀態：處理中...'
            elif status == 'DONE':
                view.status_text.value = '狀態：完成'
            elif status == 'ERROR':
                view.status_text.value = '狀態：發生錯誤'
            else:
                view.status_text.value = '狀態：閒置'

            view.progress_bar.value = progress
            view.progress_bar.color = ft.Colors.RED if is_error else ft.Colors.BLUE

            if len(logs) > view._last_rendered_log_count:
                for line in logs[view._last_rendered_log_count:]:
                    if line.strip():
                        view._append_log_line(line)
                        update_stats_from_log(view, line)
                view._last_rendered_log_count = len(logs)
                view.log_view.scroll_to(offset=-1, duration=100)

            if status in ('DONE', 'ERROR'):
                view.set_controls_disabled(False)
                if status == 'DONE' and mode:
                    view._show_extraction_summary(mode)
                view.page.update()
                break

            view.page.update()
            time.sleep(0.1)

    threading.Thread(target=poll, daemon=True).start()


def start_extraction(view, mode: str):
    snap = view.session.snapshot()
    if snap.get('status') == 'RUNNING':
        view._show_snack_bar('任務進行中...')
        return

    mods_dir = (view.mods_dir_textfield.value or '').strip()
    output_dir = (view.output_dir_textfield.value or '').strip()

    if not mods_dir:
        view._show_snack_bar('請先選擇 Mods 資料夾')
        return

    mods_path = Path(mods_dir)
    if not mods_path.exists():
        view._show_snack_bar('Mods 資料夾不存在')
        return

    if not output_dir:
        suffix = '_提取lang_輸出' if mode == 'lang' else '_提取book_輸出'
        output_dir = str(mods_path.with_name(mods_path.name + suffix))
        view.output_dir_textfield.value = output_dir
        view.page.update()
        view._append_log_line(f'[系統] 自動設定輸出路徑：{output_dir}')

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
    start_ui_poller(view, mode=mode)

    target = run_lang_extraction_service if mode == 'lang' else run_book_extraction_service
    threading.Thread(target=target, args=(mods_dir, str(out_path), view.session), daemon=True).start()


def build_preview_result_dialog(view, result: dict, mode: str):
    preview_results = result.get('preview_results', [])
    total_files = result.get('total_files', 0)
    total_size_mb = result.get('total_size_mb', 0)
    output_dir = (view.output_dir_textfield.value or '').strip()
    has_report = output_dir and Path(output_dir).exists()

    controls = [
        ft.Text(f'預覽結果（{mode.upper()}）', size=16, weight=ft.FontWeight.BOLD),
        ft.Divider(),
        ft.Text(f'共找到 {total_files} 個檔案', size=14, color=ft.Colors.BLUE_700),
        ft.Text(f'總大小：{total_size_mb:.2f} MB', size=14, color=ft.Colors.BLUE_700),
    ]

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

    controls.extend([ft.Divider(), ft.Text('詳細清單（前 20 項）：', size=13, weight=ft.FontWeight.BOLD)])
    content = ft.Column(controls, spacing=8, scroll=ft.ScrollMode.AUTO)
    for r in preview_results[:20]:
        content.controls.append(ft.Text(f"📦 {r['jar']}: {r['count']} 個檔案 ({r['size_mb']:.1f} MB)", size=12))
    if len(preview_results) > 20:
        content.controls.append(ft.Text(f"... 還有 {len(preview_results) - 20} 個 JAR 檔案", size=12, color=ft.Colors.GREY_700))

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(f'提取預覽 - {mode.upper()}'),
        content=ft.Container(content=content, width=600, height=400),
        actions=[
            ft.TextButton('取消', on_click=lambda e: view._close_dialog_overlay(dialog)),
            ft.ElevatedButton('確認提取', icon=ft.Icons.CHECK, on_click=lambda e: view._start_from_preview_overlay(dialog, mode)),
        ],
    )
    return dialog


def build_preview_error_dialog(view, error: str, mode: str):
    return ft.AlertDialog(
        modal=True,
        title=ft.Text('預覽失敗'),
        content=ft.Text(f'無法預覽 {mode.upper()} 提取：{error}'),
        actions=[ft.TextButton('關閉', on_click=lambda e: view._close_dialog_overlay(view._preview_error_dialog))],
    )


def show_preview(view, mode: str):
    mods_dir = (view.mods_dir_textfield.value or '').strip()
    if not mods_dir:
        view._show_snack_bar('請先選擇 Mods 資料夾')
        return

    mods_path = Path(mods_dir)
    if not mods_path.exists():
        view._show_snack_bar('Mods 資料夾不存在')
        return

    view._show_snack_bar(f'正在掃描 {mode.upper()} 檔案...', ft.Colors.BLUE_600)
    view._append_log_line('[系統] 開始預覽掃描...')
    view.set_controls_disabled(True)
    preview_state = PreviewState()

    def do_preview():
        from translation_tool.core.jar_processor import preview_extraction_generator
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
        while not preview_state.done:
            view.progress_bar.value = preview_state.progress
            view.progress_bar.color = ft.Colors.BLUE
            view.status_text.value = f'狀態：預覽掃描中 ({preview_state.current}/{preview_state.total})...'
            try:
                view.page.update()
            except Exception:
                pass
            time.sleep(0.1)

        view.set_controls_disabled(False)
        view.status_text.value = '狀態：預覽完成'
        view.progress_bar.value = 1.0
        view._append_log_line(f"[系統] 預覽完成：error={preview_state.error is not None}, result={preview_state.result is not None}")
        try:
            view.page.update()
        except Exception:
            pass

        if preview_state.error:
            view._append_log_line(f'[ERROR] 預覽錯誤：{preview_state.error}')
            view._show_preview_dialog_error_v2(preview_state.error, mode)
        elif preview_state.result:
            result = preview_state.result
            view._append_log_line(f"[系統] 找到 {result.get('total_files', 0)} 個檔案，準備顯示預覽對話框")
            output_dir = (view.output_dir_textfield.value or '').strip()
            if output_dir:
                try:
                    from translation_tool.core.jar_processor import generate_preview_report
                    output_path = Path(output_dir)
                    if not output_path.exists():
                        view._append_log_line(f'[系統] 輸出資料夾不存在，自動建立：{output_dir}')
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
            view._show_preview_dialog_result_v2(result, mode)
        else:
            view._append_log_line('[WARN] 預覽無結果')
            view._show_snack_bar('預覽無結果', ft.Colors.ORANGE_400)

    threading.Thread(target=poll, daemon=True).start()
