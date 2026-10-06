"""Extractor 完整提取對話框

用途：
- 點擊提取/預覽按鈕時打開的完整對話框
- 包含：進度條 + 日誌 + 結果統計
- 直接使用外部傳入的設定，無需重新設定

使用方式：
    from app.views.extractor.extractor_dialog import open_extractor_dialog
    open_extractor_dialog(page, file_picker, input_path="...", output_path="...", on_complete=..., mode="lang")
"""

import functools
import os
import threading
import traceback
import types

import flet as ft

from app.services_impl.pipelines.extract_service import (
    extract_book_files_generator,
    extract_dual_files_generator,
    extract_lang_files_generator,
    get_lang_codes,
    open_output_folder,
    prepare_extraction_paths,
    run_extraction_loop,
)
from app.ui.design import C
from app.ui.ui_batcher import UiBatcher
from app.views.extractor.extractor_dialog_ui import (
    _extractor_add_log,
    _extractor_apply_batch,
    _extractor_build_buttons,
    _extractor_build_dialog,
    _extractor_build_dual_rows,
    _extractor_build_progress_and_stats,
    _extractor_build_sections,
    _extractor_flush_ui,
    _extractor_ui_start,
    _extractor_update_progress,
    _extractor_update_stats,
)
from translation_tool.utils.log_unit import log_debug, log_info, log_warning
from translation_tool.utils.ui_mirror import in_new_task

# ============================================================
# Debug log helper (2026-07-11 規格重整)
# ============================================================
# 為什麼:user 2026-07-11 實機測試發現「預覽 → 提取」流程有幾個 dialog
# 生命週期 bug (問題 1/3/6),但 mock page 驗證無法重現真實 Flet GUI
# 行為,需要 console log 才能診斷「按鈕 click 是否真的觸發 handler」。
#
# 改用 translation_tool.utils.log_unit 提供的 log_info / log_debug /
# log_warning(已統一用 Python logging module,設定於 app 啟動時)。每行
# 透過 f"[tag] msg" prefix 維持 grep 友善(原本 print 的 [EXTRACTOR]
# prefix 由 logging formatter 統一加,這裡只負責 tag 分類)。
#
# tag → log function 對應:
#   THREAD / OPEN / PREVIEW / DIALOG → log_info (流程節點)
#   BTN                              → log_debug (按鈕觸發細節,訊息量大)
#   WARN                             → log_warning (安全網觸發需注意)


# 背景任務 → UI 的刷新間隔（秒）
_UI_FLUSH_INTERVAL_SEC = 0.2


def open_extractor_dialog(
    page: ft.Page,
    file_picker: ft.FilePicker,
    input_path: str = "",
    output_path: str = "",
    on_complete=None,
    mode: str = "lang",  # "lang", "book", "dual"
    auto_start: bool = False,  # 若 True，自動啟動提取（不需點擊「開始提取」）
    skip_zh_cn: bool = False,  # 🐛 2026-07-14 user review: 串接 skip_zh_cn_switch,主 UI 開關生效
):
    """打開提取對話框（lang / book / dual），含進度條、日誌、統計與結果摘要。

    流程:
    - 建立 dialog UI 元件 (progress_bar / log_view / status_text / stats_row / lang_row / book_row)
    - 建立 on_start_click / on_cancel_click / on_browse_click / on_close_click callbacks
    - 用 page.show_dialog(dialog) 顯示 (Flet 0.85 內建 dialog lifecycle API)

    Args:
        page: Flet Page 實例
        file_picker: Flet FilePicker 實例
        input_path: Mod 來源路徑 (mods_dir)
        output_path: 輸出目錄路徑 (留空會自動用 prepare_extraction_paths 推算)
        on_complete: 完成後的回調函式 (可選)
        mode: 提取模式 ("lang" / "book" / "dual")
        auto_start: 若 True 自動啟動提取 (不需點擊「開始提取」)
        skip_zh_cn: 是否跳過 zh_cn 抽取,從主 UI skip_zh_cn_switch 讀取。
                     只有 lang 跟 dual 模式會用到,book 模式忽略。

    Returns:
        dialog: 建立好的 Flet Dialog 實例
    """
    ctx, dialog_width = _extractor_init_paths_and_state(
        auto_start, input_path, mode, on_complete, output_path, page, skip_zh_cn
    )
    _extractor_build_progress_and_stats(ctx)
    _extractor_build_buttons(ctx)
    _extractor_bind_batching_handlers(ctx)
    info_text = _extractor_build_dual_rows(ctx)
    _extractor_bind_action_handlers(ctx)
    log_section, progress_section = _extractor_build_sections(ctx)
    _extractor_build_dialog(ctx, dialog_width, info_text, log_section, progress_section)

    # 綁定按鈕事件
    ctx.start_button.on_click = ctx.on_start_click
    ctx.cancel_button.on_click = ctx.on_cancel_click
    ctx.close_button.on_click = ctx.on_close_click
    ctx.browse_button.on_click = ctx.on_browse_click

    # 防呆安全網（2026-07-11 user 提出,modal lock 的最後一道防線）:
    # 理論上 ui_start() 內 dialog.modal=True 期間不該被外側 dismiss 觸發。
    # 但若 ESC 鍵 / 未來 Flet 版本行為變動 / 程式錯誤 真的觸發 dismiss,至少要:
    #   1. 留下 log 證據,不要讓「UI 消失但背景 thread 還在跑」完全無跡可查。
    #   2. 設 extraction_cancel_flag[0]=True,讓 background thread 在下一個 jar 檢查點提早結束,
    #      而不是空跑完 393 個 jar 沒人看結果。
    ctx.on_dialog_dismiss = functools.partial(_extractor_on_dialog_dismiss, ctx)

    ctx.dialog.on_dismiss = ctx.on_dialog_dismiss

    # 顯示對話框 (Flet 0.85 內建 dialog lifecycle API — show_dialog 自動管理 overlay + open + 父層)
    ctx.page.show_dialog(ctx.dialog)
    log_info("[OPEN] dialog shown via page.show_dialog()")

    # 如果指定了 auto_start，則自動啟動提取
    if auto_start:
        ctx.on_start_click(None)

    return ctx.dialog


def _extractor_init_paths_and_state(
    auto_start, input_path, mode, on_complete, output_path, page, skip_zh_cn
):
    """提取對話框的路徑、語系與狀態。"""
    ctx = types.SimpleNamespace(
        page=page, on_complete=on_complete, mode=mode, skip_zh_cn=skip_zh_cn
    )
    log_info(
        f"[OPEN] open_extractor_dialog mode={ctx.mode!r} auto_start={auto_start} skip_zh_cn={ctx.skip_zh_cn}"
    )
    """打開完整的提取對話框（進度+日誌+結果）。

    直接使用外部傳入的設定，無需重新設定。

    Args:
        page: Flet Page 實例
        file_picker: Flet FilePicker 實例
        input_path: Mod 來源路徑
        output_path: 輸出目錄路徑
        on_complete: 完成後的回調函式 (可選)
        mode: 提取模式 ("lang", "book", "dual")
        skip_zh_cn: 是否跳過 zh_cn 抽取(從主 UI skip_zh_cn_switch 讀取)
                     只有 lang 跟 dual 模式會用到,book 模式忽略。
    """
    dialog_width = max(600, int(ctx.page.width * 0.7))

    # 從外部取得設定
    ctx.mods_dir = input_path
    output_dir = output_path  # 可為空

    # ✅ 第一階段重構：路徑拼接邏輯已抽離至 Service 層
    # 由 prepare_extraction_paths 統一處理 config 讀取與子資料夾命名
    ctx.final_output = prepare_extraction_paths(ctx.mods_dir, ctx.mode, output_dir)

    # ✅ 階段 B-2 重構：lang_codes 讀取已抽離至 extract_service.get_lang_codes()
    # skip_zh_cn 是提取頁的單次操作值；傳入 service 讓設定預設與手動覆寫
    # 都能在建立 generator 前完成，避免先讀到一份再由 regex 二次猜測。
    ctx.lang_codes = get_lang_codes(skip_zh_cn=ctx.skip_zh_cn)

    # 狀態變數
    ctx.state = {
        "running": False,
        "done": False,
        "cancelled": False,
        "stats": {"success": 0, "warnings": 0, "failures": 0},
        "progress": 0,
        "current_file": "",
    }
    return ctx, dialog_width


def _extractor_bind_action_handlers(ctx) -> None:
    """提取工作執行緒與各按鈕 handler（依賴本模組的執行邏輯，所以留在這裡）。"""
    ctx.run_extraction = functools.partial(_extractor_run_extraction, ctx)
    ctx.on_start_click = functools.partial(_extractor_on_start_click, ctx)
    ctx.on_cancel_click = functools.partial(_extractor_on_cancel_click, ctx)
    ctx.on_close_click = functools.partial(_extractor_on_close_click, ctx)
    ctx.on_browse_click = functools.partial(_extractor_on_browse_click, ctx)


def _extractor_bind_batching_handlers(ctx) -> None:
    """批次刷新、log、進度與統計 handler。"""

    ctx.apply_batch = functools.partial(_extractor_apply_batch, ctx)

    # 節流 + 背壓：上一批還沒畫完就不再排新的刷新，避免 run_task 佇列堆積
    ctx.batcher = UiBatcher(ctx.page, ctx.apply_batch, interval=_UI_FLUSH_INTERVAL_SEC)

    ctx.flush_ui = functools.partial(_extractor_flush_ui, ctx)

    ctx.add_log = functools.partial(_extractor_add_log, ctx)

    ctx.update_progress = functools.partial(_extractor_update_progress, ctx)

    ctx.update_stats = functools.partial(_extractor_update_stats, ctx)

    # 🐛 2026-07-13 Phase 3 (user 選項 B): DUAL mode 結果顯示 LANG/BOOK 分區。
    # 為什麼:user 實機測試發現 DUAL 結果只顯示合計,看不到 LANG/BOOK 各別數字。
    # 顯示在 stats_row 下方 (只 DUAL mode 才顯示),獨立的 lang_row / book_row。
    # lang_row / book_row UI 元件在 DUAL mode 開始時(before ui_start)visible=True,
    # 結束時用 stats_data 更新文字。
    ctx.lang_row = ft.Row(
        [
            ft.Text("LANG：", size=13, color=C.DIA, weight=ft.FontWeight.BOLD),
            ft.Text("成功 ", size=13),
            ft.Text(
                "0",
                size=13,
                color=C.EM,
                weight=ft.FontWeight.BOLD,
                key="lang_success",
            ),
            ft.Text(" / 跳過 ", size=13),
            ft.Text(
                "0",
                size=13,
                color=C.GOLD,
                weight=ft.FontWeight.BOLD,
                key="lang_warnings",
            ),
        ],
        spacing=2,
        visible=False,  # DUAL mode 才顯示
    )


def _extractor_make_generator(ctx):
    """依模式（lang / book / dual）建立提取 generator。"""
    if ctx.mode == "lang":
        return extract_lang_files_generator(
            ctx.mods_dir,
            ctx.final_output,
            lang_codes=ctx.lang_codes,  # 使用配置中的所有語系
            skip_zh_cn=ctx.skip_zh_cn,
        )
    if ctx.mode == "book":
        # 🐛 2026-07-14 user review:book 模式也接 skip_zh_cn(跟 lang 模式對稱)
        return extract_book_files_generator(
            ctx.mods_dir,
            ctx.final_output,
            skip_zh_cn=ctx.skip_zh_cn,
        )
    return extract_dual_files_generator(
        ctx.mods_dir,
        ctx.final_output,
        lang_codes=ctx.lang_codes,
        skip_zh_cn=ctx.skip_zh_cn,
    )


def _extractor_on_update(ctx, update: dict) -> None:
    """run_extraction_loop callback:處理 generator yield 的進度更新。

    ⚠️ 不要在這裡判斷「整段完成」：generator 會逐 jar yield stats，也會在整段結束時 yield stats，
    真正的彙總行是在 run_extraction_loop 返回後才用 result_stats 發（見 ``_extractor_report_result``）。

    Args:
        update: dict,包含 progress / current / total / log 等 key
    """
    if "progress" in update:
        total = update.get("total", 1)
        current = update.get("current", 0)
        pct = update.get("progress", 0)
        log_msg = update.get("log", f"正在處理 {current}/{total}")
        ctx.state["progress"] = pct
        ctx.add_log(log_msg, forwarded=True)  # 核心流程 yield 的 log：後台可能已有
        ctx.update_progress(pct, log_msg)

    elif "error" in update:
        ctx.add_log(f"[ERROR] {update['error']}", level="error", forwarded=True)


def _extractor_report_result(ctx, result_stats: dict, cancelled_flag: list) -> None:
    """整段任務結束後回報取消狀態、彙總 log、進度與統計（DUAL 另顯示 LANG/BOOK 分區）。"""
    # 同步 cancelled_flag 到 state（讓 on_cancel_click 仍能正常運作）
    if cancelled_flag[0]:
        ctx.state["cancelled"] = True
        # 用 level="warning"：傳顏色字串給 level 會被 LogView 當成不在白名單而整行不顯示
        ctx.add_log("[系統] 任務已取消", level="warning", forwarded=False)

    # ✅ 真正的「整段完成」只在這裡發生（用 Service 回傳的累計 stats）
    # 避免逐 jar 誤觸發「[完成] 0/0/0」假訊息。
    ctx.state["stats"].update(
        success=result_stats["success"],
        warnings=result_stats["warnings"],
        failures=result_stats["failures"],
    )
    if cancelled_flag[0]:
        # 取消：不標記完成、不把進度拉到 100%；只回報已處理的部分
        ctx.add_log(
            f"[取消] 已處理部分：成功 {result_stats['success']} / 跳過 {result_stats['warnings']} / 失敗 {result_stats['failures']}",
            level="warning",
            forwarded=False,
        )
        ctx.update_progress(ctx.state["progress"], "已取消")
    else:
        ctx.state["done"] = True
        ctx.add_log(
            f"[完成] 成功 {result_stats['success']} / 跳過 {result_stats['warnings']} / 失敗 {result_stats['failures']}",
            level="system",
            forwarded=False,
        )
        ctx.update_progress(1.0, "任務完成")
    ctx.update_stats(
        result_stats["success"],
        result_stats["warnings"],
        result_stats["failures"],
    )
    # Phase 3 (2026-07-13) user 選項 B: DUAL mode 顯示 LANG/BOOK 分區
    if ctx.mode == "dual":
        ctx.update_dual_stats(result_stats)


def _extractor_ui_done(ctx) -> None:
    """任務完成 UI 切換:隱藏 cancel_button,顯示 close_button / browse_button / stats_row,
    dialog 解鎖 (modal=False) 讓 user 可以點外側關閉。
    """
    ctx.cancel_button.visible = False
    ctx.close_button.visible = True
    ctx.browse_button.visible = True
    ctx.stats_row.visible = True
    # 任務結束，恢復成使用者可點外側關閉
    # (modal=True 區間已過，沒有背景 thread 風險)
    ctx.dialog.modal = False
    log_info("[THREAD] ui_done: dialog.modal=False (extraction finished)")
    if ctx.on_complete:
        ctx.on_complete(ctx.state["done"], ctx.state["stats"])
    ctx.page.update()


def _extractor_run_extraction(ctx):
    """背景執行緒:執行提取。

    流程:
    - 建立輸出目錄、標記執行中、送出開始 log
    - 建立 generator（lang / book / dual）並交給 run_extraction_loop
    - 完成後發 [完成] log + 更新 stats / dual stats
    - on_complete(state["done"], state["stats"]) callback (給 caller 接續處理)
    """
    log_info("[THREAD] run_extraction thread STARTED")

    # 開始提取（先設狀態；建立輸出目錄等可能失敗的步驟都在 try 內，失敗時 finally 仍會恢復 UI）
    ctx.state["running"] = True
    ctx.state["done"] = False
    ctx.state["cancelled"] = False

    # ✅ 使用 Service 層的 run_extraction_loop 處理 Generator（選擇、cancelled 檢查、stats 解析）
    try:
        os.makedirs(ctx.final_output, exist_ok=True)
        _extractor_ui_start(ctx)
        gen = _extractor_make_generator(ctx)

        # ⭐ 每次開新任務先 reset cancel flag,然後傳 reference 給 Service
        # (on_cancel_click 修改的是同一份 list)
        ctx.extraction_cancel_flag[0] = False
        cancelled_flag = ctx.extraction_cancel_flag

        # result_stats 是整段任務最終的累計（Service 在 generator 跑完時 yield 最後一次 stats）
        result_stats = run_extraction_loop(
            gen,
            cancelled_flag=cancelled_flag,
            on_update=functools.partial(_extractor_on_update, ctx),
        )
        _extractor_report_result(ctx, result_stats, cancelled_flag)

    except Exception as ex:  # noqa: BLE001
        # 用 traceback.format_exc() 印完整堆疊,讓 user 看到錯誤根因。
        ctx.add_log(f"[ERROR] {ex}", level="error", forwarded=False)
        ctx.add_log(
            f"[TRACEBACK]\n{traceback.format_exc()}", level="error", forwarded=False
        )
        ctx.state["stats"]["failures"] = 1
        ctx.update_stats(0, 0, 1)

    finally:
        log_info(
            f"[THREAD] run_extraction finally: running=False, done={ctx.state['done']}, cancelled={ctx.state['cancelled']}"
        )
        ctx.state["running"] = False

        # 與剩餘的 log / 進度同一批在 event loop 上套用，確保順序
        # (背景 thread 直接改 dialog.modal + page.update() 不會確實同步到前端)
        ctx.batcher.set_state(on_done=functools.partial(_extractor_ui_done, ctx))
        ctx.flush_ui(force=True)


def _extractor_on_start_click(ctx, e):
    """「開始提取」按鈕 click handler。

    流程:
    - 驗證 mods_dir (留空或不存在 → SnackBar 提示,return)
    - 驗證 output_path (留空 → 自動用 prepare_extraction_paths 推算)
    - ui_start() 切換 UI (隱藏開始按鈕,顯示 cancel 跟 progress)
    - 建立並啟動 background thread 跑 run_extraction (daemon=True)

    Args:
        e: Flet ControlEvent(按鈕 click 觸發)
    """
    log_debug("[BTN] on_start_click CALLED")
    # 驗證輸入
    if not ctx.mods_dir:
        log_debug("[BTN] on_start_click rejected: mods_dir empty")
        # 🐛 UX 改進 (2026-07-13 user review):除了 dialog 內 status_text 提示,
        # 額外彈出 SnackBar 提醒 user。原因:user 按按鈕時視線多在按鈕
        # 與 textfield 上,dialog 內 status_text 在 dialog 底部容易被忽略。
        # SnackBar 從畫面底部彈出 4 秒,user 更可能注意到。
        # Modal=False 期間 SnackBar 可見(還沒進 ui_start 鎖 modal)。
        ctx.page.show_dialog(
            ft.SnackBar(
                ft.Text("⚠️ 請先選擇 Mods 資料夾"),
                open=True,
            )
        )
        ctx.status_text.value = "⚠️ 請先設定 Mod 來源"
        ctx.page.update()
        return
    if not os.path.isdir(ctx.mods_dir):
        log_debug(f"[BTN] on_start_click rejected: {ctx.mods_dir} not a dir")
        ctx.page.show_dialog(
            ft.SnackBar(
                ft.Text("⚠️ Mods 資料夾不存在"),
                open=True,
            )
        )
        ctx.status_text.value = "⚠️ Mod 來源資料夾不存在"
        ctx.page.update()
        return

    if ctx.state["running"]:
        # 已在執行中（例如連點），不重複啟動
        return
    ctx.state["running"] = True
    ctx.start_button.visible = False
    ctx.cancel_button.visible = True
    ctx.progress_bar.visible = True
    # 提取進行中鎖成 modal=True，禁止點外側關掉
    # (避免 background thread 變孤兒跑完但 UI 消失)
    ctx.dialog.modal = True
    ctx.page.update()

    # 啟動執行緒
    log_debug(
        f"[BTN] on_start_click spawning run_extraction thread (mode={ctx.mode!r})"
    )
    # 這條執行緒直接消費提取 generator（沒有 TaskSession）：給它自己的任務歸屬，
    # 核心流程與執行緒池寫出的後台記錄才分得出是哪一次提取。
    threading.Thread(
        target=in_new_task("extractor", ctx.run_extraction), daemon=True
    ).start()


def _extractor_on_cancel_click(ctx, e):
    """提取進行中按「取消」按鈕處理。

    Bug fix (2026-07-05 問題 4):
        舊實作只設 state["cancelled"]=True,但 run_extraction_loop
        是用另一份 cancelled_flag list 來偵測取消信號,舊版本按「取消」
        背景線程繼續跑,只是 UI 顯示「正在取消...」。

        修法:同時設 extraction_cancel_flag[0] = True (Service 用的那份),
        以及 state["cancelled"] = True (UI 顯示用的狀態)。
        兩份都要設是為了 UI 與 Service 同步。
    """
    log_debug("[BTN] on_cancel_click CALLED, setting cancel flags")
    ctx.extraction_cancel_flag[0] = True
    ctx.state["cancelled"] = True
    ctx.status_text.value = "正在取消..."
    ctx.page.update()


def _extractor_on_close_click(ctx, e):
    """「關閉」按鈕 click handler:用 Flet 0.85 內建 page.pop_dialog() 關閉 dialog。
    Args:
        e: Flet ControlEvent(按鈕 click 觸發)
    """
    log_debug("[BTN] on_close_click CALLED")
    # Flet 0.85 內建 API: 用 pop_dialog 關閉頂層 dialog (topmost)
    ctx.page.pop_dialog()


def _extractor_on_browse_click(ctx, e):
    """「瀏覽輸出資料夾」按鈕 click handler:呼叫 open_output_folder 開啟 final_output 資料夾。
    Args:
        e: Flet ControlEvent(按鈕 click 觸發)
    """
    # ✅ 階段 C 重構：os.startfile 已抽離至 Service 層
    open_output_folder(ctx.final_output)


def _extractor_on_dialog_dismiss(ctx, e):
    """Dialog on_dismiss callback (modal lock 防呆安全網)。

    理論上 ui_start() 內 dialog.modal=True 期間不該被外側 dismiss,
    但若 ESC 鍵 / 未來 Flet 版本行為變動 / 程式錯誤真的觸發 dismiss,
    至少要:
    1. 留下 log 證據
    2. 設 extraction_cancel_flag[0]=True,讓 background thread 在
       下一個 jar 檢查點提早結束,而不是空跑完 393 個 jar 沒人看結果

    Args:
        e: Flet ControlEvent(dismiss 觸發)
    """
    log_info(
        f"[DIALOG] dialog on_dismiss fired! running={ctx.state['running']}, done={ctx.state['done']}",
    )
    if ctx.state["running"]:
        log_warning(
            "[WARN] dialog 在提取進行中被 dismiss,background thread 會收到 cancel flag 提早結束"
            "(避免空跑到底沒人看結果)",
        )
        # 設 cancel flag — 不 mutate state(worker thread 在 finally 內自己管理 state)
        ctx.extraction_cancel_flag[0] = True


def __getattr__(name: str):
    """相容舊匯入路徑：``open_preview_dialog`` 已移到 ``extractor_preview_dialog``（延遲載入避免循環 import）。"""
    if name == "open_preview_dialog":
        from app.views.extractor.extractor_preview_dialog import open_preview_dialog

        return open_preview_dialog
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
