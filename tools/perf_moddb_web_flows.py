"""``perf_moddb_web`` 的編輯、對話框與背景工作流程情境（進階篩選、儲存、審核、批次取代、清理…）。"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from tools.perf_moddb_web_scenarios import Ctx, ScenarioError, _to_entries

EDIT_DEBOUNCE_MS = 150.0  # app.views.moddb.entries_panel.EDIT_DEBOUNCE_SECONDS
EDITOR_REGION = [598, 120, 1145, 5000]
HISTORY_REGION = [1150, 120, 1440, 5000]
ACTION_ROW = [500, 280, 1440, 360]


# ---------------------------------------------------------------------------
# 共用 helper
# ---------------------------------------------------------------------------


def _adv_open(ctx: Ctx) -> bool:
    return len(ctx.adv_dropdowns()) >= 6


def _scroll_find(
    ctx: Ctx, text, *, region, wheel_at, mode="exact", role="button", tries=10
):
    """在可捲動欄位內找按鈕；座標不在視窗內就往下滾。"""
    vh = ctx.pg.viewport_size["height"]
    for attempt in range(tries):
        for it in ctx.find_all(text, role, mode, region):
            if 90 < it[1] < vh - 20:
                return it
        ctx.pg.mouse.move(*wheel_at)
        if attempt == 2:
            ctx.pg.mouse.wheel(0, -3000)  # 可能已捲過頭：回到最上面再往下找
        else:
            ctx.pg.mouse.wheel(0, 300)
        ctx.pg.wait_for_timeout(250)
    raise ScenarioError(f"捲動後仍找不到 {text!r}")


def _list_region(ctx: Ctx):
    return [262, 410, 612, ctx.pg.viewport_size["height"] - 90]


def _list_rows(ctx: Ctx):
    return [
        r for r in ctx.find_all(None, "button", region=_list_region(ctx)) if r[3] > 40
    ]


def _select_row(ctx: Ctx, index: int) -> None:
    rows = _list_rows(ctx)
    if len(rows) <= index:
        raise ScenarioError("清單列太少")
    ctx.click_at(rows[index][0], rows[index][1])
    ctx.settle(500)


def _edit_box(ctx: Ctx):
    end = time.time() + 8
    items = ctx.find_all(None, "textbox", region=EDITOR_REGION)
    while not items and time.time() < end:
        time.sleep(0.2)
        items = ctx.find_all(None, "textbox", region=EDITOR_REGION)
    if not items:
        raise ScenarioError("找不到譯文輸入框（需要先切到「編輯譯文」）")
    return max(items, key=lambda r: r[2] * r[3])


def _open_edit_tab(ctx: Ctx) -> None:
    x, y, *_ = ctx.find("編輯譯文", region=EDITOR_REGION)
    ctx.click_at(x, y)
    ctx.settle(500)


def _dialog_button(ctx: Ctx, text, mode="exact", timeout=8.0):
    """對話框內容多半在背景載入完才出現：輪詢到按鈕出現（或逾時）。"""
    end = time.time() + timeout
    while True:
        items = ctx.find_all(text, "button", mode, [0, 0, 5000, 5000])
        if items:
            return items[0]
        if time.time() > end:
            raise ScenarioError(f"找不到對話框按鈕 {text!r}")
        time.sleep(0.1)


def _click_dialog_button(ctx: Ctx, text, mode="exact") -> None:
    x, y, *_ = _dialog_button(ctx, text, mode)
    ctx.click_at(x, y)


def _dialog_tab_button(ctx: Ctx, label: str, timeout=8.0):
    """對話框內分頁按鈕：視窗內容分段載入，按鈕出現前先等；文字含圖示字元時改用「開頭」比對。"""
    end = time.time() + timeout
    while time.time() < end:
        for mode in ("exact", "start"):
            items = ctx.find_all(label, "button", mode)
            if items:
                return items[0]
        time.sleep(0.1)
    raise ScenarioError(f"對話框裡找不到分頁按鈕 {label!r}")


def _close_dialog(ctx: Ctx, labels=("取消", "關閉")) -> None:
    """關閉最上層對話框。

    語意樹在對話框開啟／關閉的瞬間會暫時是空的，或同時留著舊頁面的節點，所以不能只看頁面
    外殼在不在：先等到看得到關閉按鈕再點，點完等「按鈕消失且外殼回來」才算關閉。
    開啟後 2 秒內都沒出現按鈕、外殼也在，視為沒有對話框；15 秒內關不掉才改按 Escape。
    """
    region = [240, 20, 1440, 1100]
    started = time.time()
    escape_at = started + 15

    def button():
        for label in labels:
            items = ctx.find_all(label, "button", "exact", region)
            if items:
                return items[0]
        return None

    def shell() -> bool:
        return bool(ctx.find_all("Mod 資料庫", "button", "exact", [0, 0, 240, 1100]))

    clicked = False
    # 語意節點很多的對話框（批量審核預覽）一次查詢要好幾秒，所以時限寬鬆，
    # 點過之後只要看到「按鈕消失、外殼回來」一次就算關閉
    while time.time() < started + 40:
        item = button()
        if item is not None:
            if time.time() - started > 0.4:
                ctx.click_at(item[0], item[1])
                clicked = True
                ctx.pg.wait_for_timeout(500)
        elif shell() and (clicked or time.time() - started > 2.0):
            ctx.pg.wait_for_timeout(300)
            return
        if time.time() > escape_at:
            ctx.pg.keyboard.press("Escape")
            escape_at = time.time() + 4
        time.sleep(0.15)
    raise ScenarioError("對話框關不掉")


def _dialog_textboxes(ctx: Ctx):
    return ctx.find_all(None, "textbox", region=[240, 20, 1440, 700])


def _open_dialog_op(ctx: Ctx, name: str, button: str, close_labels, hot=3) -> None:
    """量測「點按鈕 → 對話框顯示」，每次量完關閉；最後留一個開啟的對話框。"""

    def action(i):
        x, y, *_ = ctx.find(button, region=ACTION_ROW)
        ctx.click_at(x, y)

    ctx.op(
        name,
        action,
        after=lambda i: (ctx.settle(700), _close_dialog(ctx, close_labels)),
        hot=hot,
    )
    action(0)
    ctx.settle(900)


# ---------------------------------------------------------------------------
# 進階篩選、編輯、儲存、審核、還原
# ---------------------------------------------------------------------------


def s_advanced(ctx: Ctx) -> None:
    _to_entries(ctx)

    def toggle():
        # 標籤會隨狀態變成「進階篩選（N）」或「收合進階篩選」
        x, y, *_ = ctx.find("進階篩選", mode="contain")
        ctx.click_at(x, y)

    def ensure(open_):
        if _adv_open(ctx) != open_:
            toggle()
            ctx.settle(600)

    ctx.op("adv_open", lambda i: toggle(), before=lambda i: ensure(False), hot=3)
    ctx.settle(600)
    ctx.op("adv_close", lambda i: toggle(), before=lambda i: ensure(True), hot=3)
    ensure(True)
    # 品質（index 4）、時間依據（index 0）、排序（index 7）
    for idx, nm in ((4, "quality"), (0, "time_basis"), (7, "sort_by")):

        def before(i, idx=idx):
            ctx._target = ctx.pick_option(ctx.adv_dropdowns()[idx], (i % 2) + 1)

        ctx.op(
            f"adv_{nm}",
            lambda i: ctx.click_at(*ctx._target),
            before=before,
            hot=min(ctx.hot, 4),
        )
        ctx.settle(800)
        ctx.click_at(*ctx.pick_option(ctx.adv_dropdowns()[idx], 0))
        ctx.settle(800)

    def dir_btn(i):
        items = [
            b
            for b in ctx.find_all(None, "button", region=[260, 440, 1440, 700])
            if not b[4] and b[2] < 90 and 24 < b[3] < 56
        ]
        if not items:
            raise ScenarioError("找不到排序方向按鈕")
        ctx.click_at(items[-1][0], items[-1][1])

    ctx.op("adv_sort_direction", dir_btn, hot=min(ctx.hot, 4))
    ctx.settle(800)
    for it in ctx.find_all("清除篩選", "button", "exact", ACTION_ROW):
        ctx.click_at(it[0], it[1])
        ctx.settle(800)
    ensure(False)


def s_edit_save(ctx: Ctx) -> None:
    _to_entries(ctx)
    _select_row(ctx, 1)

    def switch(i):
        x, y, *_ = ctx.find(
            "差異預覽" if i % 2 == 0 else "編輯譯文", region=EDITOR_REGION
        )
        ctx.click_at(x, y)

    ctx.op(
        "editor_subtab_switch",
        switch,
        before=lambda i: _open_edit_tab(ctx) if i == 0 else None,
        hot=4,
    )
    _open_edit_tab(ctx)

    def key_before(i):
        x, y, *_ = _edit_box(ctx)
        ctx.click_at(x, y)
        ctx.pg.keyboard.press("Control+End")
        ctx.settle(300)

    # 編輯框停筆 0.15 秒才重畫格式提示與影響預覽（EDIT_DEBOUNCE_SECONDS）：
    # 按鍵本身的回應（儲存／審核按鈕）在 first_patch_ms，停筆後的工作量是 server_ms，
    # 含等待的時間在 wall_ms。
    ctx.op(
        "edit_keystroke",
        lambda i: ctx.pg.keyboard.type("測", delay=0),
        before=key_before,
        after_last_key=True,
        debounce_ms=EDIT_DEBOUNCE_MS,
    )
    ctx.settle(800)

    def save_before(i):
        x, y, *_ = _edit_box(ctx)
        ctx.click_at(x, y)
        ctx.pg.keyboard.press("Control+A")
        ctx.pg.keyboard.type(f"效能測試譯文{i}", delay=0)
        ctx.settle(600)

    def save_action(i):
        it = _scroll_find(ctx, "儲存", region=EDITOR_REGION, wheel_at=(840, 700))
        ctx.click_at(it[0], it[1])

    ctx.op("save_manual", save_action, before=save_before, hot=min(ctx.hot, 4))
    ctx.settle(1000)

    def revert_action(i):
        it = ctx.find("還原這次更新", region=HISTORY_REGION)
        ctx.click_at(it[0], it[1])

    ctx.op("revert_history", revert_action, hot=min(ctx.hot, 2))


def _unreviewed_row(ctx: Ctx, i: int):
    """清單目前可見的「AI 機翻、尚未審核」條目（沒有時往下捲動清單）。``i`` 只用來挑第幾個。"""
    for _ in range(12):
        rows = [
            r
            for r in _list_rows(ctx)
            if "未翻譯" not in r[4] and "人工" not in r[4] and "簡中轉繁" not in r[4]
        ]
        if rows:
            return rows[0]
        ctx.pg.mouse.move(440, 700)
        ctx.pg.mouse.wheel(0, 450)
        ctx.pg.wait_for_timeout(350)
    raise ScenarioError("找不到未審核的 AI 機翻條目")


def _click_review_button(ctx: Ctx):
    it = _scroll_find(
        ctx,
        "審核（確認目前譯文）",
        region=EDITOR_REGION,
        wheel_at=(840, 700),
        mode="start",
    )
    ctx.click_at(it[0], it[1])


def _open_review_dialog(ctx: Ctx, i: int) -> None:
    """選一筆未審核條目、點「審核」並等確認視窗載入完成（不計時的準備步驟）。"""
    row = _unreviewed_row(ctx, i)
    ctx.click_at(row[0], row[1])
    ctx.settle(600)
    for attempt in range(3):
        _click_review_button(ctx)
        try:
            _dialog_button(ctx, "確認審核", "start", timeout=5.0)
            return
        except ScenarioError:
            if attempt == 2:
                ctx.snap("review_dialog_missing")
                raise
    ctx.settle(300)


def s_review(ctx: Ctx) -> None:
    _to_entries(ctx)

    ctx.op(
        "review_open_confirm",
        lambda i: _click_review_button(ctx),
        before=lambda i: _select_unreviewed(ctx, i),
        hot=min(ctx.hot, 3),
        after=lambda i: (ctx.settle(700), _close_dialog(ctx, ("取消",))),
    )
    ctx.op(
        "review_confirm",
        lambda i: _click_dialog_button(ctx, "確認審核", "start"),
        before=lambda i: _open_review_dialog(ctx, i + 4),
        hot=min(ctx.hot, 3),
    )
    ctx.settle(1000)


def _select_unreviewed(ctx: Ctx, i: int) -> None:
    row = _unreviewed_row(ctx, i)
    ctx.click_at(row[0], row[1])
    ctx.settle(600)


def s_chars(ctx: Ctx) -> None:
    _to_entries(ctx)
    _select_row(ctx, 0)

    def action(i):
        items = ctx.find_all(None, "switch", region=[598, 120, 1145, 1100])
        if not items:
            raise ScenarioError("找不到字元檢視開關")
        ctx.click_at(items[0][0], items[0][1])

    ctx.op("chars_toggle", action, hot=4)


# ---------------------------------------------------------------------------
# 對話框：批次取代 / 批量審核 / 清理 / 智慧校對 / 學名
# ---------------------------------------------------------------------------


def s_batch_replace(ctx: Ctx) -> None:
    _to_entries(ctx)
    _open_dialog_op(ctx, "batch_replace_open", "批次取代", ("取消",))
    ctx.wait_for("批次尋找與取代", role=None)
    boxes = _dialog_textboxes(ctx)
    ctx.click_at(boxes[0][0], boxes[0][1])
    ctx.pg.keyboard.type("Next", delay=5)
    ctx.click_at(boxes[1][0], boxes[1][1])
    ctx.pg.keyboard.type("Nxt", delay=5)
    ctx.op(
        "batch_replace_preview", lambda i: _click_dialog_button(ctx, "產生預覽"), hot=2
    )
    ctx.wait_for("可替換", role=None)
    ctx.op(
        "batch_replace_confirm_scope",
        lambda i: _click_dialog_button(ctx, "檢查並確認寫入範圍"),
        hot=0,
    )
    ctx.wait_for("我已核對", role=None)
    ctx.settle(500)
    for it in ctx.find_all("我已核對", None, "contain")[:1]:
        ctx.click_at(it[0], it[1])
    ctx.settle(300)
    ctx.snap("batch_confirm")
    ctx.op(
        "batch_replace_execute",
        lambda i: _click_dialog_button(ctx, "確認並開始不可取消的寫入"),
        hot=0,
    )
    ctx.settle(1500)
    ctx.snap("batch_done")
    _close_dialog(ctx)
    ctx.settle(800)

    # 被取代的條目不一定是目前選取的那筆：搜尋取代後的文字，選第一筆才看得到整批還原按鈕
    ctx.set_search("Nxt")
    ctx.settle(1500)
    _select_row(ctx, 0)

    def undo(i):
        it = _scroll_find(
            ctx,
            "整批還原這次取代",
            region=HISTORY_REGION,
            wheel_at=(1290, 700),
        )
        ctx.click_at(it[0], it[1])

    ctx.op("batch_replace_undo", undo, hot=0)
    ctx.settle(800)


def s_bulk_review(ctx: Ctx) -> None:
    _to_entries(ctx)
    _open_dialog_op(ctx, "bulk_review_open", "批量審核", ("取消", "關閉"))
    ctx.op(
        "bulk_review_preview",
        lambda i: _click_dialog_button(ctx, "產生預覽"),
        hot=2,
        quiet_ms=5000,  # 全版本預覽在背景要數秒，期間沒有 patch
    )
    ctx.settle(500)
    ctx.snap("bulk_preview")
    # 全版本預覽納入數十萬筆；執行與還原改在單一模組（數百筆）上量，才是互動可用的規模
    dropdowns = ctx.find_all(None, "button", region=[480, 230, 700, 290])
    if not dropdowns:
        raise ScenarioError("找不到批量審核的模組下拉選單")
    ctx.click_at(*ctx.pick_option(dropdowns[0], 1))
    ctx.settle(700)
    ctx.op(
        "bulk_review_preview_module",
        lambda i: _click_dialog_button(ctx, "產生預覽"),
        hot=1,
    )
    ctx.settle(500)
    ctx.snap("bulk_selected")
    ctx.op(
        "bulk_review_execute",
        lambda i: _click_dialog_button(ctx, "確認審核", "contain"),
        hot=0,
    )
    ctx.settle(1500)
    ctx.snap("bulk_done")
    ctx.op(
        "bulk_review_undo",
        lambda i: _click_dialog_button(ctx, "還原本次批量審核"),
        hot=0,
    )
    ctx.settle(1000)
    _close_dialog(ctx, ("關閉", "取消"))


def s_cleanup(ctx: Ctx) -> None:
    """清理：開啟 → 匯出檢查檔 → 預覽 → 套用刪除 → 還原（需要 ``--seed-cleanup``）。"""
    x, y, *_ = ctx.tab("總覽")
    ctx.click_at(x, y)
    ctx.settle(900)

    def open_action(i):
        x, y, *_ = ctx.find("資料清理")
        ctx.click_at(x, y)

    ctx.op(
        "cleanup_open",
        open_action,
        after=lambda i: (ctx.settle(700), _close_dialog(ctx, ("關閉",))),
        hot=3,
    )
    open_action(0)
    ctx.settle(900)
    ctx.op("cleanup_export", lambda i: _click_dialog_button(ctx, "匯出檢查檔"), hot=1)
    ctx.settle(800)
    ctx.op("cleanup_preview", lambda i: _click_dialog_button(ctx, "預覽刪除"), hot=1)
    ctx.settle(600)
    ctx.snap("cleanup_preview")
    if not ctx.find_all("確認刪除 0 筆", "button", "contain"):
        ctx.op(
            "cleanup_apply",
            lambda i: _click_dialog_button(ctx, "確認刪除", "start"),
            hot=0,
        )
        ctx.settle(1500)
        ctx.snap("cleanup_applied")
        ctx.op(
            "cleanup_revert",
            lambda i: _click_dialog_button(ctx, "還原選取的清理"),
            hot=0,
        )
        ctx.settle(1500)
        ctx.snap("cleanup_reverted")
    else:
        ctx.notes_add("cleanup 沒有可刪項目（請用 --seed-cleanup 建立）")
    _close_dialog(ctx, ("關閉",))


def s_smart_review(ctx: Ctx) -> None:
    _to_entries(ctx)
    _open_dialog_op(ctx, "smart_review_open", "智慧校對", ("關閉",))
    ctx.snap("smart_open")
    for label in ("歷史批次", "審查", "執行"):

        def tab_action(i, label=label):
            x, y, *_ = _dialog_tab_button(ctx, label)
            ctx.click_at(x, y)

        ctx.op(f"smart_review_tab_{label}", tab_action, hot=2)
        ctx.settle(800)
    boxes = ctx.find_all(None, "textbox", region=[240, 20, 1440, 900])
    if boxes:

        def est_before(i):
            ctx.click_at(boxes[0][0], boxes[0][1])
            ctx.pg.keyboard.press("Control+A")
            ctx.pg.keyboard.press("Backspace")
            ctx.settle(900)

        ctx.op(
            "smart_review_estimate",
            lambda i: ctx.pg.keyboard.type(["alt", "ctrl", "key"][i % 3], delay=0),
            before=est_before,
            hot=2,
        )
    _close_dialog(ctx, ("關閉",))


def s_species(ctx: Ctx) -> None:
    _to_entries(ctx)
    _open_dialog_op(ctx, "species_open", "掃描學名", ("關閉",))
    ctx.op("species_scan", lambda i: _click_dialog_button(ctx, "掃描候選"), hot=1)
    ctx.settle(800)
    ctx.snap("species_scan")
    _close_dialog(ctx, ("關閉",))


# ---------------------------------------------------------------------------
# 掃描匯入 / 批次機翻
# ---------------------------------------------------------------------------


def s_scan(ctx: Ctx) -> None:
    folder = ctx.work / "scan_jars"
    folder.mkdir(exist_ok=True)
    src = Path(ctx.args.scan_source) if getattr(ctx.args, "scan_source", None) else None
    if src and src.is_dir() and not list(folder.glob("*.jar")):
        for jar in sorted(src.glob("*.jar"))[:3]:
            shutil.copyfile(jar, folder / jar.name)
    if not list(folder.glob("*.jar")):
        raise ScenarioError("沒有掃描用 jar（用 --scan-source 指定含 jar 的資料夾）")
    x, y, *_ = ctx.tab("掃描匯入")
    ctx.click_at(x, y)
    ctx.settle(900)
    # 遊戲版本用一個資料庫沒有的版本，真實掃描不會動到既有資料
    version_box = ctx.find_all(None, "textbox", region=[270, 200, 820, 280])
    if not version_box:
        raise ScenarioError("找不到遊戲版本輸入框")
    ctx.click_at(version_box[0][0], version_box[0][1])
    ctx.pg.keyboard.type("9.9.9", delay=0)
    ctx.settle(600)
    boxes = ctx.find_all(None, "textbox", region=[800, 100, 1340, 500])
    if not boxes:
        raise ScenarioError("找不到資料夾輸入框")
    ctx.click_at(boxes[0][0], boxes[0][1])
    ctx.pg.keyboard.type(str(folder), delay=0)
    ctx.settle(600)

    def dry(i):
        x, y, *_ = ctx.find("先預覽（不寫入）", region=[260, 100, 1340, 5000])
        ctx.click_at(x, y)

    ctx.op("scan_dry_run", dry, hot=1)
    ctx.settle(800)
    ctx.snap("scan_dry")

    def real(i):
        x, y, *_ = ctx.find("開始掃描", mode="start", region=[260, 100, 1340, 5000])
        ctx.click_at(x, y)

    ctx.op("scan_real", real, hot=1)
    ctx.settle(1000)
    ctx.snap("scan_real")


def _translate_dds(ctx: Ctx):
    return [
        i
        for i in ctx.find_all(None, "button", region=[260, 150, 1340, 330])
        if not i[4] and abs(i[3] - 48) < 3
    ]


def s_translate_scope(ctx: Ctx) -> None:
    x, y, *_ = ctx.tab("批次機翻")
    ctx.click_at(x, y)
    ctx.settle(1200)
    if len(_translate_dds(ctx)) < 2:
        raise ScenarioError("找不到批次機翻範圍下拉選單")

    def before(i):
        ctx._target = ctx.pick_option(_translate_dds(ctx)[1], (i % 3) + 1)

    ctx.op(
        "translate_scope_mod",
        lambda i: ctx.click_at(*ctx._target),
        before=before,
        hot=4,
    )
    ctx.settle(800)
    boxes = ctx.find_all(None, "textbox", region=[700, 150, 1340, 330])
    if boxes:

        def lim_before(i):
            ctx.click_at(boxes[0][0], boxes[0][1])
            ctx.pg.keyboard.press("Control+A")
            ctx.pg.keyboard.press("Backspace")
            ctx.settle(700)

        # 一次打三個數字：從最後一個字的事件起算（字與字之間是瀏覽器送鍵盤事件的間隔）
        ctx.op(
            "translate_scope_limit",
            lambda i: ctx.pg.keyboard.type(str(100 * (i + 1)), delay=0),
            before=lim_before,
            hot=3,
            after_last_key=True,
        )
        ctx.settle(700)

    def preview(i):
        x, y, *_ = ctx.find("先預覽（不呼叫 AI）", region=[260, 100, 1340, 5000])
        ctx.click_at(x, y)

    ctx.op("translate_preview_no_ai", preview, hot=2)
    ctx.settle(800)


def s_version(ctx: Ctx) -> None:
    """切換版本下拉選單（資料庫有多個版本時，每次都是真正的切換）。"""
    _to_entries(ctx)

    def before(i):
        ctx._target = ctx.pick_option(ctx.dropdowns()[0], (i + 1) % 2)

    ctx.op(
        "version_switch",
        lambda i: ctx.click_at(*ctx._target),
        before=before,
        hot=min(ctx.hot, 4),
    )
    ctx.settle(800)


def s_review_clear(ctx: Ctx) -> None:
    """人工審核後再「解除人工審核」；量測的是解除按鈕。"""
    _to_entries(ctx)

    def confirm_review(i):
        _open_review_dialog(ctx, i)
        _click_dialog_button(ctx, "確認審核", "start")
        ctx.settle(900)

    def action(i):
        it = _scroll_find(
            ctx,
            "解除人工審核",
            region=EDITOR_REGION,
            wheel_at=(840, 700),
            mode="start",
        )
        ctx.click_at(it[0], it[1])

    ctx.op("review_clear", action, before=confirm_review, hot=min(ctx.hot, 3))
    ctx.settle(800)


FLOWS = {
    "advanced": s_advanced,
    "edit": s_edit_save,
    "review": s_review,
    "version": s_version,
    "review_clear": s_review_clear,
    "chars": s_chars,
    "batch_replace": s_batch_replace,
    "bulk_review": s_bulk_review,
    "cleanup": s_cleanup,
    "smart_review": s_smart_review,
    "species": s_species,
    "scan": s_scan,
    "translate": s_translate_scope,
}
