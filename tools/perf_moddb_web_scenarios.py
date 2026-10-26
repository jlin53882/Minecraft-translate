"""``tools/perf_moddb_web.py`` 的量測情境：用 Playwright 對真實 Flet Web 的 Mod 資料庫頁操作。

每個情境函式簽名為 ``scenario(ctx)``，在 ``ctx.op(...)`` 內做「準備（不計時） → 動作（計時）」。
情境彼此獨立；任何一個失敗只記錄錯誤，不影響其他情境。
"""

from __future__ import annotations

import contextlib
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INIT_JS = """
() => {
  if (window.__perf) return true;
  const state = {last: 0, t0: 0, mut: 0, vis: 0, hash: 0, times: []};
  window.__perf = state;
  const mark = () => { state.t0 = performance.now(); };
  window.addEventListener('pointerup', mark, true);
  window.addEventListener('keydown', mark, true);
  const host = document.querySelector('flt-semantics-host') || document.body;
  new MutationObserver((records) => { state.last = performance.now(); state.mut += records.length; if (state.times.length < 400) state.times.push(state.last); })
    .observe(host, {subtree: true, childList: true, characterData: true, attributes: true});
  return true;
}
"""

FIND_JS = """
([text, role, mode, region, nth]) => {
  const out = [];
  for (const e of document.querySelectorAll('flt-semantics')) {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (role === 'textbox') {
      if (!e.querySelector(':scope > input, :scope > textarea:not([disabled])')) continue;
    } else if (role && e.getAttribute('role') !== role) continue;
    if (region) {
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      if (cx < region[0] || cy < region[1] || cx > region[2] || cy > region[3]) continue;
    }
    const t = (e.innerText || e.getAttribute('aria-label') || '').trim();
    const first = t.split('\\n')[0].trim();
    let ok;
    if (text === null) ok = true;
    else if (mode === 'exact') ok = t === text || first === text;
    else if (mode === 'start') ok = t.startsWith(text);
    else ok = t.includes(text);
    if (ok) out.push([r.left + r.width / 2, r.top + r.height / 2, r.width, r.height, t.slice(0, 200)]);
  }
  out.sort((a, b) => (a[1] - b[1]) || (a[0] - b[0]));
  return out;
}
"""


class ScenarioError(RuntimeError):
    """情境前置條件不符（例如找不到按鈕）。"""


class Ctx:
    def __init__(self, page, drv, args, work: Path) -> None:
        self.pg = page
        self.drv = drv
        self.args = args
        self.work = work
        self.hot = args.hot
        self.shots = Path(args.screenshots) if args.screenshots else None

    # -- 低階 -----------------------------------------------------------------
    def find_all(self, text, role="button", mode="exact", region=None):
        return self.pg.evaluate(FIND_JS, [text, role, mode, region, 0])

    def find(self, text, role="button", mode="exact", region=None, nth=0):
        items = self.find_all(text, role, mode, region)
        if len(items) <= nth:
            raise ScenarioError(
                f"找不到 {role} {text!r}（mode={mode}, region={region}）"
            )
        return items[nth]

    def click_at(self, x, y):
        self.pg.mouse.click(x, y)

    def wait_for(self, text, role=None, mode="contain", region=None, timeout=15.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.find_all(text, role, mode, region):
                return True
            time.sleep(0.05)
        raise ScenarioError(f"等不到 {text!r}")

    def gone(self, text, role=None, mode="contain", region=None, timeout=15.0):
        end = time.time() + timeout
        while time.time() < end:
            if not self.find_all(text, role, mode, region):
                return True
            time.sleep(0.05)
        raise ScenarioError(f"{text!r} 沒有消失")

    def notes_add(self, text):
        print("  note:", text, flush=True)

    def snap(self, name):
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
            self.pg.screenshot(path=str(self.shots / f"{name}.png"))

    def settle(self, ms=None):
        self.drv.settle(ms)

    # -- 常用控制項 -------------------------------------------------------------
    def sidebar(self, text):
        x, y, *_ = self.find(text, region=[0, 0, 240, 1100])
        self.click_at(x, y)

    def ensure_moddb(self):
        """確保目前在 Mod 資料庫頁（情境可單獨執行）。"""
        for _ in range(3):
            if self.find_all("條目校對", "button", "exact", [900, 80, 1440, 130]):
                return
            self.pg.keyboard.press("Escape")
            self.pg.wait_for_timeout(400)
            for label in ("取消", "關閉"):
                if self.find_all(label, "button", "exact", [240, 20, 1440, 1100]):
                    x, y, *_ = self.find(
                        label, "button", "exact", [240, 20, 1440, 1100]
                    )
                    self.click_at(x, y)
                    self.pg.wait_for_timeout(400)
        self.sidebar("Mod 資料庫")
        self.wait_for(
            "條目校對", role="button", mode="exact", region=[900, 80, 1440, 130]
        )
        self.settle(600)

    def tab(self, label):
        """ModDB 頁籤分段按鈕（頁首右側）。"""
        return self.find(label, region=[900, 80, 1440, 130])

    def dropdowns(self):
        """條目頁篩選卡片的下拉選單（沒有文字的 48px 高按鈕），由上到下、左到右。"""
        items = [
            i
            for i in self.find_all(None, role="button", region=[260, 180, 1440, 300])
            if not i[4] and abs(i[3] - 48) < 3
        ]
        return items

    def pick_option(self, dd, index):
        """展開下拉選單並點第 index 個選項（不計時）。"""
        x, y, _w, h, _ = dd
        self.click_at(x, y)
        self.pg.wait_for_timeout(350)
        return (x, y + h / 2 + 30 + 44 * index)

    def set_search(self, text, *, clear=True):
        x, y, *_ = self.search_box()
        self.click_at(x, y)
        if clear:
            self.pg.keyboard.press("Control+A")
            self.pg.keyboard.press("Backspace")
        if text:
            self.pg.keyboard.type(text, delay=5)

    def adv_dropdowns(self):
        """進階篩選面板（y>330）的下拉選單，依列、欄排序。"""
        items = [
            i
            for i in self.find_all(None, role="button", region=[260, 330, 1440, 700])
            if not i[4] and abs(i[3] - 48) < 3
        ]
        return sorted(items, key=lambda i: (round(i[1] / 20), i[0]))

    def search_box(self):
        items = self.find_all(None, role="textbox", region=[260, 170, 1440, 260])
        if not items:
            raise ScenarioError("找不到搜尋框")
        return items[-1]

    # -- 量測 ---------------------------------------------------------------
    def op(self, name, action, *, before=None, after=None, hot=None, label=""):
        """冷 1 次 + 熱 N 次。``before(i)`` 為不計時的準備；``action(i)`` 只做原始輸入。"""
        n = 1 + (self.hot if hot is None else hot)
        for i in range(n):
            if before:
                before(i)
            self.settle()
            self.drv.measure(name, lambda i=i: action(i), label=f"{label}#{i}")
            if after:
                after(i)
        if getattr(self.args, "profile", False):
            self._profile_once(name, action, before, after, n)
        return self.drv.results.get(name, [])

    def _profile_once(self, name, action, before, after, i) -> None:
        """多做一次熱操作並對事件迴圈做 cProfile（結果寫到 work/profiles/）。"""
        if before:
            before(i)
        self.settle()
        self.drv.profile_start()
        self.drv.measure(f"_prof_{name}", lambda: action(i), label="profile")
        text = self.drv.profile_stop()
        out = self.work / "profiles"
        out.mkdir(exist_ok=True)
        (out / f"{name}.txt").write_text(text, encoding="utf-8")
        self.drv.results.pop(f"_prof_{name}", None)
        if after:
            after(i)


# ---------------------------------------------------------------------------
# 情境
# ---------------------------------------------------------------------------


def s_open_page(ctx: Ctx) -> None:
    """開啟 ModDB 頁：冷（首次建構）→ 熱（離開再回來）。"""

    def before(i):
        if i > 0:
            ctx.sidebar("工作台")
            ctx.settle(600)

    def action(i):
        x, y, *_ = ctx.find("Mod 資料庫", region=[0, 0, 240, 1100])
        ctx.click_at(x, y)

    ctx.op("open_page", action, before=before)
    ctx.wait_for("條目校對", role="button")


def s_tabs(ctx: Ctx) -> None:
    ctx.tab("總覽")
    ctx.settle(500)
    for label in ("條目校對", "掃描匯入", "批次機翻", "總覽"):

        def before(i, label=label):
            other = "總覽" if label != "總覽" else "條目校對"
            if i > 0:
                x, y, *_ = ctx.tab(other)
                ctx.click_at(x, y)
                ctx.settle(500)

        def action(i, label=label):
            x, y, *_ = ctx.tab(label)
            ctx.click_at(x, y)

        ctx.op(f"tab_{label}", action, before=before)
        ctx.settle(500)


def s_overview(ctx: Ctx) -> None:
    ctx.tab("總覽")
    ctx.settle(800)

    def action(i):
        x, y, *_ = ctx.find("重新整理")
        ctx.click_at(x, y)

    ctx.op("overview_refresh", action)


def _to_entries(ctx: Ctx) -> None:
    """切到條目校對並等清單載入完成（至少 3 列條目；頁碼按鈕與「沒有資料」不算）。"""
    x, y, *_ = ctx.tab("條目校對")
    ctx.click_at(x, y)
    vh = ctx.pg.viewport_size["height"]
    end = time.time() + 60
    while time.time() < end:
        rows = [
            r
            for r in ctx.find_all(None, "button", region=[262, 410, 612, vh - 150])
            if r[3] > 30
        ]
        loading = ctx.find_all("背景更新條目資料中", None, "contain")
        empty = ctx.find_all("沒有資料", None, "contain")
        if len(rows) >= 3 and not loading and not empty:
            break
        time.sleep(0.2)
    else:
        ctx.snap("to_entries_timeout")
        raise ScenarioError("條目清單沒有載入（<3 列或仍顯示沒有資料）")
    ctx.settle(800)


def s_paging(ctx: Ctx) -> None:
    _to_entries(ctx)
    # 第一頁 → 第 2 頁 → 中間頁 → 最後一頁 → 回第一頁
    vh = ctx.pg.viewport_size["height"]
    pager_region = [260, vh - 140, 620, vh - 30]

    def click_page(label):
        x, y, *_ = ctx.find(str(label), region=pager_region)
        ctx.click_at(x, y)

    def to_first():
        buttons = [
            b for b in ctx.find_all(None, "button", region=pager_region) if b[4] == "1"
        ]
        if buttons:
            ctx.click_at(buttons[0][0], buttons[0][1])
            ctx.settle(400)

    # 下一頁（含目前頁碼往後）：反覆點「2」「3」…
    def next_action(i):
        click_page(i + 2)

    ctx.op(
        "page_next_sequential",
        next_action,
        before=lambda i: to_first() if i == 0 else None,
        hot=min(ctx.hot, 4),
    )

    def last_action(i):
        buttons = ctx.find_all(None, "button", region=pager_region)
        digits = [b for b in buttons if b[4].isdigit()]
        target = max(digits, key=lambda b: int(b[4]))
        ctx.click_at(target[0], target[1])

    def back_first(i):
        to_first()

    ctx.op("page_last", last_action, before=back_first)
    to_first()


def s_select_row(ctx: Ctx) -> None:
    _to_entries(ctx)
    vh = ctx.pg.viewport_size["height"]
    rows = [r for r in ctx.find_all(None, "button", region=[262, 410, 612, vh - 90])]
    if len(rows) < 3:
        raise ScenarioError("清單列太少")

    def action(i):
        rs = [r for r in ctx.find_all(None, "button", region=[262, 410, 612, vh - 90])]
        r = rs[(i % 2) + 1]
        ctx.click_at(r[0], r[1])

    ctx.op("select_row", action)


def s_state_filter(ctx: Ctx) -> None:
    _to_entries(ctx)
    labels = ["未翻譯", "全部"]

    def action(i):
        x, y, *_ = ctx.find(labels[i % 2], region=[270, 250, 880, 300])
        ctx.click_at(x, y)

    ctx.op("filter_state", action)


def s_dropdown_filters(ctx: Ctx) -> None:
    _to_entries(ctx)
    dds = ctx.dropdowns()
    if len(dds) < 5:
        raise ScenarioError(f"下拉選單數量不足：{len(dds)}")
    for idx, nm in ((1, "mod"), (2, "kind"), (3, "source"), (4, "search_field")):

        def before(i, idx=idx):
            dd = ctx.dropdowns()[idx]
            ctx._target = ctx.pick_option(dd, (i % 2) + 1 if idx != 4 else (i % 3) + 1)

        def action(i):
            ctx.click_at(*ctx._target)

        ctx.op(f"filter_dropdown_{nm}", action, before=before, hot=min(ctx.hot, 4))
        ctx.settle(600)
        # 還原為第一個選項
        dd = ctx.dropdowns()[idx]
        t = ctx.pick_option(dd, 0)
        ctx.click_at(*t)
        ctx.settle(800)


def s_search(ctx: Ctx) -> None:
    _to_entries(ctx)
    words = ["alt", "ctrl", "shift", "key", "scroll", "gui"]

    def before(i):
        ctx.set_search("")
        ctx.settle(900)
        x, y, *_ = ctx.search_box()
        ctx.click_at(x, y)

    def action(i):
        # 輸入最後一個字元的時間才算（debounce 0.35s 後才查詢）
        ctx.pg.keyboard.type(words[i % len(words)], delay=0)

    ctx.op("search_typing_debounced", action, before=before)
    ctx.set_search("")
    ctx.settle(900)


def s_search_incremental(ctx: Ctx) -> None:
    """像人一樣逐字輸入（字元間隔 ``gap_ms``）：量「最後一個字 → 清單更新」。

    每個字之間的停頓若超過 debounce，就會每個字元各查一次；這裡驗證舊查詢被中斷、不堆積。
    """
    _to_entries(ctx)
    words = ["scroll", "inventory", "gui", "alt", "ctrl", "button"]
    gap_ms = 260  # 每個字都超過 debounce：最壞情況（每字元都查詢）

    def before(i):
        ctx.set_search("")
        ctx.settle(900)
        x, y, *_ = ctx.search_box()
        ctx.click_at(x, y)

    def action(i):
        ctx.pg.keyboard.type(words[i % len(words)], delay=gap_ms)

    ctx.op("search_typing_slow", action, before=before, hot=min(ctx.hot, 4))
    ctx.set_search("")
    ctx.settle(900)


def s_search_enter(ctx: Ctx) -> None:
    _to_entries(ctx)

    def before(i):
        ctx.set_search("")
        ctx.settle(900)
        ctx.pg.keyboard.type(
            ["alt", "ctrl", "gui", "key", "mod", "tab"][i % 6], delay=0
        )
        ctx.settle(900)

    def action(i):
        ctx.pg.keyboard.press("Enter")

    ctx.op("search_enter", action, before=before)
    ctx.set_search("")
    ctx.settle(900)


SCENARIOS = {
    "open": s_open_page,
    "tabs": s_tabs,
    "overview": s_overview,
    "paging": s_paging,
    "select": s_select_row,
    "state": s_state_filter,
    "dropdowns": s_dropdown_filters,
    "search": s_search,
    "search_enter": s_search_enter,
    "search_slow": s_search_incremental,
}


def _register_flows() -> None:
    from tools.perf_moddb_web_flows import FLOWS

    SCENARIOS.update(FLOWS)


def open_app(pg, port: int) -> None:
    pg.goto(f"http://127.0.0.1:{port}/")
    end = time.time() + 90
    while time.time() < end:
        time.sleep(0.5)
        pg.evaluate(
            "() => { const p = document.querySelector('flt-semantics-placeholder'); if (p) p.click(); }"
        )
        if pg.locator("flt-semantics[role=button]").count() > 5:
            break
    else:
        raise SystemExit("App 未載入（找不到 semantics 按鈕）")
    pg.evaluate(INIT_JS)
    pg.wait_for_timeout(500)


def run_all(args, port: int, probe_port: int, work: Path, Driver) -> dict:
    from playwright.sync_api import sync_playwright

    import tools.ui_smoke_offline  # noqa: F401 - 攔截 CanvasKit / 字型 CDN

    width, height = (int(v) for v in args.viewport.split("x"))
    _register_flows()
    wanted = [w for w in args.only.split(",") if w] or list(SCENARIOS)
    errors: dict[str, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        drv = None
        for name in wanted:
            fn = SCENARIOS[name]
            print(f"== {name}", flush=True)
            # 每個情境用全新的瀏覽器 context（= 新的 Flet session），避免前一個情境留下對話框或篩選
            context = browser.new_context(viewport={"width": width, "height": height})
            pg = context.new_page()
            pg.on("pageerror", lambda e: errors.setdefault("_pageerror", str(e)[:300]))
            try:
                open_app(pg, port)
                if drv is None:
                    drv = Driver(pg, probe_port, args.quiet_ms, args.timeout_ms)
                drv.page = pg
                drv.drain()
                ctx = Ctx(pg, drv, args, work)
                if name != "open":
                    ctx.ensure_moddb()
                fn(ctx)
            except Exception as exc:  # noqa: BLE001 - 單一情境失敗不影響其他
                errors[name] = f"{type(exc).__name__}: {exc}"
                traceback.print_exc(file=sys.stderr)
                with contextlib.suppress(Exception):
                    ctx.snap(f"error_{name}")
            finally:
                with contextlib.suppress(Exception):
                    context.close()
        browser.close()
    summary = {k: summarize(v) for k, v in drv.results.items()}
    stalls = {
        k: [
            {"ms": s["ms"], "stack": s["stacks"][-1] if s["stacks"] else ""} for s in v
        ][:6]
        for k, v in drv.stalls.items()
    }
    return {"summary": summary, "raw": drv.results, "stalls": stalls, "errors": errors}


def summarize(samples: list[dict]) -> dict:
    from tools.perf_moddb_web import summarize as base

    return base(samples)


def print_table(output: dict) -> None:
    print()
    header = f"{'操作':34} {'冷srv':>7} {'冷e2e':>7} | {'熱srv med':>9} {'p95':>7} {'首patch':>7} | {'熱e2e med':>9} {'p95':>7} | {'blk':>6} {'patchKB':>7}"
    print(header)
    print("-" * len(header))
    for name, s in output["summary"].items():
        cold = s["cold"]
        print(
            f"{name:34} {cold.get('server_ms') or 0:7.0f} {cold.get('e2e_ms') or 0:7.0f} | "
            f"{s.get('hot_server_ms_median', 0):9.0f} {s.get('hot_server_ms_p95', 0):7.0f} "
            f"{s.get('hot_first_patch_ms_median', 0):7.0f} | "
            f"{s.get('hot_e2e_ms_median', 0):9.0f} {s.get('hot_e2e_ms_p95', 0):7.0f} | "
            f"{s.get('hot_loop_block_ms_median', 0):6.0f} {cold.get('patch_kb') or 0:7.1f}"
        )
    listed = {
        n: s for n, s in output["summary"].items() if "hot_list_patch_ms_median" in s
    }
    if listed:
        print(
            "\n清單內容最後一次 patch（收到事件 → ListView，ms；輸入類另列「最後一個字之後」）："
        )
        for name, s in listed.items():
            extra = ""
            if "hot_after_last_key_ms_median" in s:
                extra = (
                    f"   最後一個字之後 med {s['hot_after_last_key_ms_median']:7.0f}"
                    f"  p95 {s['hot_after_last_key_ms_p95']:7.0f}"
                )
            print(
                f"  {name:32} 熱 med {s['hot_list_patch_ms_median']:7.0f}  p95 {s['hot_list_patch_ms_p95']:7.0f}{extra}"
            )
    for name, s in output["summary"].items():
        if s.get("db_last_hot"):
            calls = ", ".join(f"{k}={v:.0f}" for k, v in s["db_last_hot"].items())
            print(f"  db[{name}]: {calls}")
    for name, stalls in output["stalls"].items():
        print(
            f"  stall[{name}]: "
            + "; ".join(f"{s['ms']}ms {s['stack']}" for s in stalls[:2])
        )
    if output["errors"]:
        print("\n錯誤：")
        for key, msg in output["errors"].items():
            print(f"  {key}: {msg}")
