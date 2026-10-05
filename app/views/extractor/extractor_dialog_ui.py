"""提取對話框的控制項建構與 UI 更新輔助（log／進度／統計；皆只依賴 ctx）。"""

import functools

import flet as ft

from app.ui.design import C
from app.views._log import LogView
from translation_tool.utils.ui_mirror import mirror_to_backend


def _extractor_build_progress_and_stats(ctx) -> None:
    """進度條、日誌與結果統計控制項。"""
    # ⭐ extraction_cancel_flag (Bug fix 2026-07-05 問題 4):
    #   提取進行中按「取消」按鈕真的中斷 Service 的關鍵。
    #   原本 on_cancel_click 只設 state["cancelled"]=True,但
    #   run_extraction_loop 是用另一份 cancelled_flag list 來偵測取消,
    #   所以舊版本按「取消」背景線程繼續跑,只是 UI 顯示「正在取消...」。
    #
    #   修法:outer-scope list 讓 on_cancel_click 能修改同一份 reference,
    #   Service run_extraction_loop 會在每次 for update in generator:
    #   檢查 cancelled_flag[0],看到 True 就 return stats 結束任務。
    #
    #   為什麼不用 page.overlay.remove:這個修法完全不動 overlay,
    #   只動 closure 變數,按鈕 event binding 不會受影響。
    ctx.extraction_cancel_flag = [False]

    # ========== UI 元件 - 進度條 ==========
    ctx.progress_bar = ft.ProgressBar(
        value=0, height=10, visible=False, bgcolor=C.TRACK, color=C.DIA
    )
    ctx.status_text = ft.Text("等待任務啟動...", size=13, color=C.MUTED)
    ctx.progress_pct = ft.Text("0%", size=12, color=C.MUTED, weight=ft.FontWeight.BOLD)

    # ========== UI 元件 - 日誌 ==========
    # PR refactor/unified-log-view: 改用 LogView widget
    # 統一深色容器 + 等寬字 + 等級顏色（從 theme）
    ctx.log_view = LogView(
        page=ctx.page,
        mode="append",
        max_lines=2000,
    )

    # ========== UI 元件 - 結果統計 ==========
    ctx.stats_success = ft.Text("0", size=14, color=C.EM, weight=ft.FontWeight.BOLD)
    ctx.stats_warnings = ft.Text("0", size=14, color=C.GOLD, weight=ft.FontWeight.BOLD)
    ctx.stats_failures = ft.Text("0", size=14, color=C.RED, weight=ft.FontWeight.BOLD)


def _extractor_build_buttons(ctx) -> None:
    """按鈕與 UI 刷新輔助函式。"""

    ctx.stats_row = ft.Row(
        [
            ft.Text("結果：", size=13),
            ft.Text("成功 ", size=13),
            ctx.stats_success,
            ft.Text(" / 跳過 ", size=13),
            ctx.stats_warnings,
            ft.Text(" / 失敗 ", size=13),
            ctx.stats_failures,
        ],
        spacing=2,
        visible=False,
    )

    # ========== 按鈕 ==========
    ctx.start_button = ft.Button(
        "開始提取",
        icon=ft.Icons.PLAY_ARROW,
        bgcolor=C.EM,
        color=C.ON_EM,
    )
    ctx.cancel_button = ft.Button(
        "取消",
        icon=ft.Icons.STOP,
        visible=False,
    )
    ctx.close_button = ft.Button(
        "關閉",
        icon=ft.Icons.CLOSE,
        visible=False,
    )
    ctx.browse_button = ft.Button(
        "開啟輸出資料夾",
        icon=ft.Icons.FOLDER_OPEN,
        visible=False,
    )

    # ========== 輔助函式 ==========
    # 背景執行緒只把 log / 進度寫進緩衝區；實際修改控制項與 page.update()
    # 一律排到 Flet event loop（page.run_task），並以 _UI_FLUSH_INTERVAL_SEC 節流。
    # 避免 worker 執行緒與 event loop 同時改控制項（object_patch IndexError），
    # 也避免數百個 JAR 各觸發一次整頁 diff。
    ctx.run_on_ui = functools.partial(_extractor_run_on_ui, ctx)


def _extractor_build_dual_rows(ctx):
    """DUAL 分區統計列與資訊文字。"""
    ctx.book_row = ft.Row(
        [
            ft.Text("BOOK：", size=13, color=C.ENCH, weight=ft.FontWeight.BOLD),
            ft.Text("成功 ", size=13),
            ft.Text(
                "0",
                size=13,
                color=C.EM,
                weight=ft.FontWeight.BOLD,
                key="book_success",
            ),
            ft.Text(" / 跳過 ", size=13),
            ft.Text(
                "0",
                size=13,
                color=C.GOLD,
                weight=ft.FontWeight.BOLD,
                key="book_warnings",
            ),
        ],
        spacing=2,
        visible=False,  # DUAL mode 才顯示
    )

    ctx.update_dual_stats = functools.partial(_extractor_update_dual_stats, ctx)

    # ========== 資訊顯示 ==========
    info_text = ft.Text(
        f"來源：{ctx.mods_dir}\n輸出：{ctx.final_output}",
        size=12,
        color=C.MUTED,
    )
    return info_text


def _extractor_build_sections(ctx):
    """資訊、進度與日誌區塊。"""

    # ========== 建立對話框 ==========
    progress_section = ft.Column(
        [
            ft.Row(
                [ctx.status_text, ctx.progress_pct],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ctx.progress_bar,
        ],
        spacing=4,
    )

    log_section = ft.Container(
        content=ctx.log_view,
        bgcolor=C.LOG_BG,
        border_radius=8,
        height=250,
        padding=10,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )
    return log_section, progress_section


def _extractor_build_dialog(
    ctx, dialog_width, info_text, log_section, progress_section
) -> None:
    """對話框本體。"""

    ctx.dialog = ft.AlertDialog(
        modal=False,
        title=ft.Row(
            [
                ft.Icon(ft.Icons.DOWNLOAD, size=24, color=C.DIA),
                ft.Text(
                    f"提取資源 - {ctx.mode.upper()}", size=18, weight=ft.FontWeight.BOLD
                ),
            ],
            spacing=10,
        ),
        content=ft.Container(
            content=ft.Column(
                [
                    # 資訊區域
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Text("設定", weight=ft.FontWeight.BOLD, size=14),
                                info_text,
                            ],
                            spacing=4,
                        ),
                        padding=10,
                        bgcolor=C.PANEL2,
                        border_radius=8,
                    ),
                    ft.Divider(),
                    # 進度區域
                    progress_section,
                    ft.Divider(),
                    # 日誌區域
                    log_section,
                    # 結果統計
                    ctx.stats_row,
                    # Phase 3 (2026-07-13) DUAL mode LANG/BOOK 分區
                    ctx.lang_row,
                    ctx.book_row,
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            ),
            width=dialog_width,
            height=min(600, int(ctx.page.height * 0.8)),
        ),
        actions=[
            ctx.start_button,
            ctx.cancel_button,
            ctx.close_button,
            ctx.browse_button,
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )


def _extractor_run_on_ui(ctx, fn):
    """把 fn 排到 Flet event loop 執行（run_task 需要 coroutine function）。"""

    async def _apply():
        fn()

    ctx.page.run_task(_apply)


def _extractor_apply_batch(ctx, lines, state):
    """在 event loop 上一次套用累積的 log / 進度。"""
    if lines:
        ctx.log_view.add_many(lines)
    if "progress" in state:
        val, text = state["progress"]
        ctx.progress_bar.value = val
        ctx.status_text.value = text
        ctx.progress_pct.value = f"{int(val * 100)}%"
    ctx.page.update()
    if state.get("on_done"):
        # 最後一批 log 畫完後才切換完成狀態
        state["on_done"]()


def _extractor_flush_ui(ctx, force: bool = False):
    """把累積的 log / 進度交給 event loop（節流；force=True 一定送出）。"""
    ctx.batcher.flush(force=force)


def _extractor_add_log(ctx, msg: str, level: str = "info"):
    """PR refactor/unified-log-view: 改用 LogView 統一處理等級顏色。

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
    # 批次推畫面的路徑不經過 LogView.add，所以在入口鏡像到後台（已記錄過的內容會去重）
    mirror_to_backend(msg, level)
    ctx.batcher.add_lines([(f">> {msg}", level)])
    ctx.flush_ui()


def _extractor_update_progress(ctx, val: float, text: str):
    """更新 progress_bar / status_text / progress_pct 三個 UI 元件（節流批次）。

    Args:
        val: 進度百分比 (0.0 ~ 1.0)
        text: 狀態文字 (顯示在 progress_pct 上方)
    """
    ctx.batcher.set_state(progress=(val, text))
    ctx.flush_ui()


def _extractor_update_stats(ctx, success, warnings, failures):
    """更新 stats_success / stats_warnings / stats_failures 文字。

    Args:
        success: 成功數
        warnings: 跳過數
        failures: 失敗數
    """

    def apply():
        ctx.stats_success.value = str(success)
        ctx.stats_warnings.value = str(warnings)
        ctx.stats_failures.value = str(failures)
        ctx.page.update()

    ctx.run_on_ui(apply)


def _extractor_update_dual_stats(ctx, result_stats):
    """DUAL mode 完成時,把 lang / book sub-dict 寫進各自的 Text。

    :param result_stats: run_extraction_loop 回傳的完整 stats dict,
                          含 lang / book sub-dict。
    """

    def apply():
        lang = result_stats.get("lang") or {}
        book = result_stats.get("book") or {}
        # 找對應的 Text 元件(透過 key 屬性)
        for row in (ctx.lang_row, ctx.book_row):
            for ctrl in row.controls:
                if getattr(ctrl, "key", None) == "lang_success" and row is ctx.lang_row:
                    ctrl.value = str(lang.get("success", 0))
                elif (
                    getattr(ctrl, "key", None) == "lang_warnings"
                    and row is ctx.lang_row
                ):
                    ctrl.value = str(lang.get("warnings", 0))
                elif (
                    getattr(ctrl, "key", None) == "book_success" and row is ctx.book_row
                ):
                    ctrl.value = str(book.get("success", 0))
                elif (
                    getattr(ctrl, "key", None) == "book_warnings"
                    and row is ctx.book_row
                ):
                    ctrl.value = str(book.get("warnings", 0))
        ctx.lang_row.visible = True
        ctx.book_row.visible = True
        ctx.page.update()

    ctx.run_on_ui(apply)


def _extractor_ui_start(ctx) -> None:
    """任務開始時送出的 log 與初始進度。

    按鈕與 modal 切換已在 on_start_click（UI 執行緒）完成，這裡只送 log。
    """
    ctx.update_progress(0, "開始任務...")
    ctx.add_log(f"[系統] 開始提取 ({ctx.mode})...", level="system")
    ctx.add_log(f"[系統] 來源：{ctx.mods_dir}", level="system")
    ctx.add_log(f"[系統] 輸出：{ctx.final_output}", level="system")
