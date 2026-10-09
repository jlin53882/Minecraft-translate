"""Extractor 預覽對話框（lang / book / dual）。

由 ``extractor_dialog.py`` 拆出（#114）：先掃描預測結果，使用者按「確認執行」後才開啟提取對話框。
``open_extractor_dialog`` 在呼叫時才從 ``extractor_dialog`` 取得（避免循環 import，
也讓測試可以 patch ``extractor_dialog.open_extractor_dialog``）。
"""

import asyncio
import functools
import types

import flet as ft

from app.services_impl.pipelines.extract_service import (
    prepare_preview_paths,
    preview_extraction_generator,
)
from app.tasks.operation_registry import launch_page_operation
from app.ui.design import C
from app.views._log import LogView
from app.views.extractor import extractor_dialog as _extractor_dialog
from app.views.extractor.extractor_dialog_helpers import format_size
from app.views.extractor.extractor_state import PreviewState
from translation_tool.utils.cancellation import cancel_scope
from translation_tool.utils.log_unit import log_error, log_info, log_warning
from translation_tool.utils.ui_mirror import in_new_task, new_task_id

# 背景任務 → UI 的刷新間隔（秒）
_UI_FLUSH_INTERVAL_SEC = 0.2


def _preview_file_count(result: dict, mode: str) -> int:
    """預覽結果中單一 JAR 的可提取檔案數。"""
    if mode == "dual":
        return int(result.get("lang_count", 0) or 0) + int(
            result.get("book_count", 0) or 0
        )
    return int(result.get("count", 0) or 0)


def open_preview_dialog(
    page: ft.Page,
    file_picker: ft.FilePicker,
    input_path: str = "",
    output_path: str = "",
    mode: str = "lang",
    skip_zh_cn: bool = False,  # 🐛 2026-07-14 user review: 串接 skip_zh_cn_switch,preview 路徑也生效
):
    """打開預覽對話框（lang / book / dual），先掃描預測結果,user 確認後再執行提取。

    流程:
    - 透過 start_scan + do_scan + ui_poller 背景執行緒跑預覽掃描
    - 結果 mutate preview_dialog 的 title/content/actions(單一 dialog 模式)
    - 達 100% 完成時,變成「確認執行 / 取消」按鈕
    - user 按「確認執行」後呼叫 on_complete callback 走提取流程

    Args:
        page: Flet Page 實例
        file_picker: Flet FilePicker 實例
        input_path: Mod 來源路徑
        output_path: 提取輸出目錄 (留空則由 prepare_extraction_paths 推算)
        mode: 預覽模式 ("lang" / "book" / "dual")
        skip_zh_cn: 是否跳過 zh_cn,從主 UI skip_zh_cn_switch 讀取 (跟 extract 模式對齊)

    Returns:
        dialog: 建立好的 Flet Dialog 實例
    """
    ctx, dialog_width = _preview_build_widgets(
        file_picker, input_path, mode, output_path, page, skip_zh_cn
    )
    _preview_bind_handlers(ctx)
    _preview_build_dialog(ctx, dialog_width)

    ctx.preview_dialog.on_dismiss = ctx.on_preview_dismiss

    ctx.page.show_dialog(ctx.preview_dialog)

    return ctx.preview_dialog


def _preview_build_widgets(
    file_picker, input_path, mode, output_path, page, skip_zh_cn
):
    """預覽對話框的資訊、進度與日誌控制項。"""
    ctx = types.SimpleNamespace(
        page=page,
        file_picker=file_picker,
        input_path=input_path,
        output_path=output_path,
        mode=mode,
        skip_zh_cn=skip_zh_cn,
    )
    log_info(
        f"[PREVIEW] open_preview_dialog mode={ctx.mode!r} skip_zh_cn={ctx.skip_zh_cn}"
    )
    """打開預覽對話框（lang / book / dual）。

    預覽流程完全在本模組內實現，不依賴 extractor_actions 模組的 legacy show_preview：
    - 透過 start_scan + do_scan + ui_poller 背景執行緒跑預覽掃描
    - 結果 mutate preview_dialog 的 title/content/actions（單一 dialog 模式）
    - 掃描完成進度達 100% 時，自動展開「確認執行 / 取消」按鈕

    Args:
        page: Flet Page 實例
        file_picker: Flet FilePicker 實例
        input_path: Mod 來源路徑
        output_path: 輸出目錄路徑
        mode: 預設模式
    """
    dialog_width = max(500, int(ctx.page.width * 0.5))

    # ========== UI 元件 ==========
    ctx.info_text = ft.Text(
        f"來源：{ctx.input_path}\n輸出：{ctx.output_path}\n模式：{ctx.mode}",
        size=12,
        color=C.MUTED,
    )

    # 進度區
    # 🐛 Bug 修復：初始狀態文字應為「等待開始」而非「正在掃描」
    ctx.status_text = ft.Text("等待開始預覽...", size=13, color=C.MUTED)
    ctx.progress_pct = ft.Text("--", size=12, color=C.MUTED, weight=ft.FontWeight.BOLD)
    return ctx, dialog_width


def _preview_bind_handlers(ctx) -> None:
    """預覽 handler 與掃描狀態。"""
    # 🐛 Bug 修復：明確設定 progress_bar 的顏色與背景色，避免渲染不明顯
    ctx.progress_bar = ft.ProgressBar(
        value=0,
        height=8,
        bgcolor=C.TRACK,
        color=C.DIA,
    )

    # 日誌區
    # PR refactor/unified-log-view: 改用 LogView widget
    # 統一深色容器 + 等寬字 + 等級顏色（從 theme）
    ctx.log_view = LogView(
        page=ctx.page,
        mode="append",
        max_lines=2000,
        height=200,
    )

    ctx.add_log = functools.partial(_preview_add_log, ctx)

    ctx.show_result_dialog = functools.partial(_preview_show_result_dialog, ctx)

    # ========== 執行掃描 ==========
    ctx.state = {"running": False, "cancelled": False, "done": False}
    ctx.preview_state = PreviewState()

    ctx.start_scan = functools.partial(_preview_start_scan, ctx)

    # ========== 建立對話框 ==========
    ctx.start_button = ft.Button(
        "開始預覽",
        icon=ft.Icons.SEARCH,
        on_click=lambda e: ctx.start_scan(),
    )


def _preview_build_dialog(ctx, dialog_width) -> None:
    """預覽對話框本體。"""

    ctx.preview_dialog = ft.AlertDialog(
        modal=False,
        title=ft.Row(
            [
                ft.Icon(ft.Icons.SEARCH, size=24, color=C.DIA),
                ft.Text(
                    f"預覽 - {ctx.mode.upper()}", size=18, weight=ft.FontWeight.BOLD
                ),
            ]
        ),
        content=ft.Container(
            content=ft.Column(
                [
                    ctx.info_text,
                    ft.Divider(),
                    ft.Row(
                        [ctx.status_text, ctx.progress_pct],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ctx.progress_bar,
                    # log_view 已是 LogView widget（自帶深色容器 + 圓角）
                    ctx.log_view,
                ],
                spacing=10,
            ),
            width=dialog_width,
            height=500,
        ),
        actions=[ctx.start_button],
    )

    # 2026-07-12 user 建議:預覽 dialog 防呆安全網(與主 dialog 的 on_dialog_dismiss 對稱設計)。
    # 理論上 start_scan() 內 preview_dialog.modal=True 期間,使用者不該能點外側 dismiss。
    # 但萬一 (ESC 鍵 / Flet 行為改變 / 程式錯誤) 真的觸發 dismiss,至少要:
    #   1. 留下 log 證據
    #   2. 設 state["cancelled"] = True — do_scan() 的 for 迴圈本來就有檢查這個旗標,
    #      下一個 generator yield 就 break,background thread 提早結束而非空跑到底。
    ctx.on_preview_dismiss = functools.partial(_preview_on_preview_dismiss, ctx)


def _preview_add_log(
    ctx,
    msg,
    level: str = "info",
    update: bool = True,
    *,
    forwarded: bool = False,
    task: object | None = None,
):
    """PR refactor/unified-log-view: 改用 LogView.add() 統一處理等級顏色。

    level: debug/info/warning/error/system，預設 info
    從 msg 字串前綴（[系統 / [ERROR / [完成）也能推斷 level
    """
    # 從 msg 前綴推斷 level（向後兼容既有呼叫）
    if level == "info":
        if msg.startswith("[系統"):
            level = "system"
        elif msg.startswith("[ERROR"):
            level = "error"
        elif msg.startswith("[完成"):
            level = "system"
    ctx.log_view.add(
        f">> {msg}",
        level=level,
        update=update,
        mirror_text=msg,
        dedupe=forwarded,
        task=task,
    )


def _preview_result_controls(ctx, result: dict) -> list:
    """預覽結果畫面的內容控制項（統計、JAR 清單）。"""
    preview_results = result.get("preview_results", [])
    total_files = result.get("total_files", 0)
    total_size_mb = result.get("total_size_mb", 0)

    controls = [
        ft.Text(f"預覽結果（{ctx.mode.upper()}）", size=16, weight=ft.FontWeight.BOLD),
        ft.Text(f"輸出（確認執行後）：{ctx.output_path}", size=12, color=C.MUTED),
        ft.Divider(),
    ]

    if ctx.mode == "dual":
        total_lang = sum(r.get("lang_count", 0) for r in preview_results)
        total_book = sum(r.get("book_count", 0) for r in preview_results)
        controls.append(ft.Text(f"Lang：{total_lang} 個", size=14, color=C.DIA))
        controls.append(ft.Text(f"Book：{total_book} 個", size=14, color=C.DIA))
    else:
        controls.append(ft.Text(f"共找到 {total_files} 個檔案", size=14, color=C.DIA))

    controls.append(ft.Text(f"總大小：{total_size_mb:.2f} MB", size=14, color=C.DIA))

    # 只列出有可提取檔案的 JAR（406 個 JAR 時大多是 0 個檔案，清單會被淹沒）
    with_files = [r for r in preview_results if _preview_file_count(r, ctx.mode) > 0]
    empty_count = len(preview_results) - len(with_files)
    header = f"詳細清單（{len(with_files)} 個 JAR 有可提取檔案）："
    controls.extend([ft.Divider(), ft.Text(header, size=13, weight=ft.FontWeight.BOLD)])
    if empty_count:
        controls.append(
            ft.Text(
                f"另有 {empty_count} 個 JAR 沒有可提取的檔案，已略過不列出",
                size=12,
                color=C.MUTED,
            )
        )

    jar_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)
    for r in with_files:
        if ctx.mode == "dual":
            jar_list.controls.append(
                ft.Text(
                    f"📦 {r['jar']}: Lang {r.get('lang_count', 0)} 個 / Book {r.get('book_count', 0)} 個",
                    size=12,
                )
            )
        else:
            # 🐛 2026-07-14 user review:小檔案顯示 0.0 MB(user 看不出來)
            # 改用 format_size helper 自動選擇單位 (MB / KB / B)
            # format_size 從頂部 import(extractor_dialog_helpers)
            jar_list.controls.append(
                ft.Text(
                    f"📦 {r['jar']}: {r['count']} 個檔案 ({format_size(r['size_mb'])})",
                    size=12,
                )
            )

    list_container = ft.Container(
        content=jar_list,
        height=300,
        padding=5,
        bgcolor=C.PANEL2,
        border_radius=8,
    )
    controls.append(list_container)
    return controls


def _preview_start_extraction(ctx, e):
    """確認執行 — 沿用合併後的單一 dialog,只 pop 一次。"""
    log_info("[PREVIEW] start_extraction CALLED (確認執行 clicked)")
    output_path = _preview_resolve_output_path(ctx)
    ctx.page.pop_dialog()  # 只有 preview_dialog 一個 dialog,pop 一次就乾淨
    log_info(
        "[PREVIEW] start_extraction: preview_dialog closed via pop_dialog (single)"
    )
    # 直接開啟提取對話框並自動啟動(不需點擊「開始提取」)
    log_info(
        "[PREVIEW] start_extraction: calling open_extractor_dialog auto_start=True"
    )
    _extractor_dialog.open_extractor_dialog(
        ctx.page,
        ctx.file_picker,
        input_path=ctx.input_path,
        output_path=output_path,
        mode=ctx.mode,
        auto_start=True,
        skip_zh_cn=ctx.skip_zh_cn,  # 預覽時的「跳過 zh_cn」要帶到實際提取
    )
    log_info("[PREVIEW] start_extraction: open_extractor_dialog returned")


def _preview_show_result_dialog(ctx, result):
    """「確認執行」流程改用單一 dialog(沿用 preview_dialog),換內容呈現結果清單。

    治本作法 (2026-07-11 由 user 提出):
    過去用「疊 result_dialog 在 preview_dialog 上」的模式,在 modal=False
    的 preview_dialog 可能被使用者點外側提前 dismiss 後,「疊兩層」
    的假設就會被打破,留下 / 誤 pop dialog。

    改成只留 preview_dialog 一個 dialog,掃描完成時直接把它的 title /
    content / actions 換成「結果清單」畫面。Stack 永遠深度 ≤ 1,
    pop_dialog() 呼叫一次就足夠(就算 preview_dialog 已被使用者提前
    dismiss,pop 找不到東西會靜默 return None,不會波及其他 dialog)。
    """
    log_info(
        f"[PREVIEW] show_result_dialog: {len(result.get('preview_results', []))} JARs found"
    )
    controls = _preview_result_controls(ctx, result)

    # 在原本的 preview_dialog 上 mutate title/content/actions,而不是開新 dialog
    log_info(
        "[PREVIEW] show_result_dialog: mutating preview_dialog in-place (single-dialog mode)"
    )
    ctx.preview_dialog.title = ft.Row(
        [
            ft.Icon(ft.Icons.CHECK_CIRCLE, size=24, color=C.EM),
            ft.Text(
                f"提取預覽 - {ctx.mode.upper()}", size=18, weight=ft.FontWeight.BOLD
            ),
        ]
    )
    ctx.preview_dialog.content = ft.Container(
        content=ft.Column(controls, spacing=8, scroll=ft.ScrollMode.AUTO),
        width=600,
        height=500,
    )
    ctx.preview_dialog.actions = [
        ft.TextButton(
            "取消",
            on_click=lambda e: (
                log_info("[PREVIEW] 取消 CALLED → pop_dialog (single)"),
                ctx.page.pop_dialog(),  # 只有一個 dialog,pop 一次就夠
            ),
        ),
        ft.Button(
            "確認執行",
            icon=ft.Icons.CHECK,
            on_click=functools.partial(_preview_start_extraction, ctx),
        ),
    ]
    # 2026-07-12:解鎖 preview_dialog modal(掃描已結束,沒有 background thread 風險)。
    # 對應 start_scan() 內的 preview_dialog.modal = True 鎖定。
    ctx.preview_dialog.modal = False
    log_info("[PREVIEW] show_result_dialog: preview_dialog.modal=False (scan finished)")
    ctx.page.update()  # ← 重新 paint preview_dialog,內容現在是「結果列表」
    log_info("[PREVIEW] show_result_dialog: preview_dialog mutated, page.update() done")


def _preview_reset_scan_state(ctx) -> None:
    """掃描開始前重設 state 與 preview_state。"""
    ctx.state["running"] = True
    ctx.state["cancelled"] = False
    ctx.state["done"] = False
    # ⭐ Bug fix (2026-07-11 問題 6): 必須重設 preview_state.done = False,
    # 否則上一次掃描完成的 done=True 會讓這次的 ui_poller thread
    # 進入 while 迴圈後立刻退出 (line 683 `while not preview_state.done and ...`),
    # 導致 _do_finalize 看到 final_result=None 什麼都不做,
    # user 看到「按了開始預覽沒反應」但 generator 確實有跑。
    ctx.preview_state.done = False
    ctx.preview_state.progress = 0
    ctx.preview_state.current = 0
    ctx.preview_state.total = 0
    ctx.preview_state.result = None
    ctx.preview_state.error = None
    ctx.preview_state.log = ""


def _preview_do_scan(ctx):
    """背景執行緒：跑 generator，只寫入 preview_state（不碰任何控制項）。"""
    generator = preview_extraction_generator(
        ctx.input_path, ctx.mode, skip_zh_cn=ctx.skip_zh_cn
    )
    try:
        with cancel_scope(lambda: ctx.state["cancelled"]):
            for update in generator:
                if ctx.state["cancelled"]:
                    break
                if "progress" in update:
                    ctx.preview_state.progress = update.get("progress", 0)
                    ctx.preview_state.current = update.get("current", 0)
                    ctx.preview_state.total = update.get("total", 0)
                    ctx.preview_state.log = update.get("log", "")
                if "error" in update:
                    ctx.preview_state.error = update["error"]
                    break
                if "result" in update:
                    ctx.preview_state.result = update["result"]
    except Exception as ex:  # noqa: BLE001 - 錯誤要回報到 UI
        log_error(f"[提取預覽] 掃描失敗：{ex!r}", exc_info=True)
        ctx.preview_state.error = str(ex)
    finally:
        close = getattr(generator, "close", None)
        if callable(close):
            close()
        # 不論成功、失敗或取消都要標記完成，避免 UI poller 永遠等待
        ctx.preview_state.done = True


async def _preview_ui_poller(ctx):
    """在 Flet event loop 上輪詢 preview_state，節流更新進度與 log。

    只在 event loop 上改控制項，背景執行緒不直接呼叫 page.update()。
    """
    last_log = None
    while True:
        # A cancel request is not worker completion. Keep the dialog locked until
        # _preview_do_scan's finally block reports that the worker has actually exited.
        finished = ctx.preview_state.done
        ctx.progress_bar.value = ctx.preview_state.progress
        ctx.progress_pct.value = f"{int(ctx.preview_state.progress * 100)}%"
        cur_log = getattr(ctx.preview_state, "log", None)
        if cur_log:
            ctx.status_text.value = cur_log
            if cur_log != last_log:
                # 掃描流程的 log：poller 跑在 UI 執行緒（沒有任務歸屬），明確帶掃描工作的任務識別
                ctx.add_log(
                    cur_log,
                    update=False,
                    forwarded=True,
                    task=getattr(ctx, "scan_task", None),
                )
                last_log = cur_log
        if ctx.state["cancelled"] and not finished:
            ctx.status_text.value = "正在取消..."
        if finished:
            break
        ctx.page.update()
        await asyncio.sleep(_UI_FLUSH_INTERVAL_SEC)

    final_result = ctx.preview_state.result
    final_error = ctx.preview_state.error
    ctx.start_button.disabled = False
    ctx.state["running"] = False

    cancelled = ctx.state["cancelled"]
    if not cancelled and not final_error:
        ctx.progress_bar.value = 1.0
        ctx.progress_pct.value = "100%"
        ctx.status_text.value = "預覽完成"

    if final_error:
        ctx.add_log(
            f"[ERROR] {final_error}", level="error", update=False, forwarded=False
        )
        ctx.status_text.value = f"預覽失敗：{final_error}"
        ctx.progress_bar.value = 0
        ctx.progress_pct.value = "--"
        # 掃描已結束：解除 start_scan() 的 modal 鎖定，否則使用者無法關閉對話框
        ctx.preview_dialog.modal = False
        ctx.page.update()
    elif final_result and not cancelled:
        results = final_result.get("preview_results", [])
        ctx.add_log(
            f"[完成] 找到 {len(results)} 個 JAR",
            level="system",
            update=False,
            forwarded=False,
        )
        ctx.show_result_dialog(final_result)
    else:
        if cancelled:
            ctx.status_text.value = "已取消"
        ctx.preview_dialog.modal = False
        ctx.page.update()


def _preview_start_scan(ctx):
    """按鈕：開始預覽掃描"""
    log_info(
        f"[PREVIEW] start_scan CALLED (mode={ctx.mode!r}, state.running={ctx.state['running']})"
    )
    if ctx.state["running"]:
        log_info("[PREVIEW] start_scan rejected: state.running=True")
        return
    # 預覽只掃描不寫檔；顯示「確認執行」後實際的提取輸出位置
    # （原本把預覽資料夾當成提取輸出，導致結果多一層或寫進預覽資料夾）
    output_path = _preview_resolve_output_path(ctx)
    if output_path:
        ctx.info_text.value = (
            f"來源：{ctx.input_path}\n"
            f"輸出（確認執行後）：{output_path}\n"
            f"模式：{ctx.mode}"
        )
        ctx.page.update()

    _preview_reset_scan_state(ctx)

    # 2026-07-12 user 建議:預覽掃描進行中鎖 modal=True,避免點外側 dismiss 後
    # dialog 變孤兒(do_scan / ui_poller thread 仍繼續跑,但 UI 已不在)。
    # 解鎖時機在 show_result_dialog() mutate 完之後(backgound thread 已結束)。
    ctx.preview_dialog.modal = True
    log_info("[PREVIEW] start_scan: preview_dialog.modal=True (scanning)")

    # 重置 UI
    ctx.log_view.clear()
    ctx.progress_bar.value = 0
    ctx.progress_pct.value = "0%"
    ctx.status_text.value = "正在掃描..."
    ctx.start_button.disabled = True
    ctx.page.update()

    ctx.add_log(
        f"[系統] 開始預覽 {ctx.mode.upper()} 掃描...", level="system", forwarded=False
    )

    ctx.scan_task = new_task_id("extract-preview")

    def request_operation_cancel() -> None:
        ctx.state["cancelled"] = True

    launched = launch_page_operation(
        ctx.page,
        in_new_task(
            "extract-preview",
            functools.partial(_preview_do_scan, ctx),
            task=ctx.scan_task,
        ),
        name="JAR 提取預覽",
        owner="extractor-preview",
        on_cancel=request_operation_cancel,
    )
    if not launched:
        ctx.state["running"] = False
        ctx.preview_state.done = True
        ctx.preview_dialog.modal = False
        ctx.start_button.disabled = False
        ctx.status_text.value = "應用程式正在關閉，未啟動掃描"
        ctx.page.update()
        ctx.page.show_dialog(
            ft.SnackBar(ft.Text("應用程式正在關閉，無法啟動新任務"), open=True)
        )
        return

    async def poller():
        await _preview_ui_poller(ctx)

    ctx.page.run_task(poller)


def _preview_resolve_output_path(ctx) -> str:
    """解析並保存預覽確認時要使用的輸出路徑。

    預覽畫面與後續提取共用同一個解析結果，避免只顯示預設路徑、確認時卻重新
    解析成不同位置。
    """
    # The first call snapshots either the explicit user path or the configured
    # preview suffix. Later calls (including confirmation) pass that snapshot
    # back as an explicit path, so configuration changes cannot move the target.
    ctx.output_path = prepare_preview_paths(ctx.input_path, ctx.mode, ctx.output_path)
    return ctx.output_path


def _preview_on_preview_dismiss(ctx, e):
    """Preview dialog on_dismiss callback (modal lock 防呆安全網)。

    理論上 ui_start() 內 dialog.modal=True 期間不該被外側 dismiss,
    但若真的觸發 dismiss,設 state["cancelled"]=True 讓 do_scan / ui_poller
    thread 在下一個檢查點提早結束。

    Args:
        e: Flet ControlEvent(dismiss 觸發)
    """
    log_info(
        f"[PREVIEW] preview_dialog on_dismiss fired! state.running={ctx.state['running']}, cancelled={ctx.state['cancelled']}",
    )
    if ctx.state["running"]:
        log_warning(
            "[WARN] preview_dialog 在掃描中被 dismiss,do_scan / ui_poller thread 會收到 cancel 旗標提早結束",
        )
        ctx.state["cancelled"] = True
