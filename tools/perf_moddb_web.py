"""真實 Flet Web 的 Mod 資料庫頁效能量測。

用法（需要 Playwright + Chromium，離線環境會攔截 CanvasKit / 字型 CDN）::

    python tools/perf_moddb_web.py run --db path/to/moddb.db --out result.json
    python tools/perf_moddb_web.py run --db mini.db --only tabs,paging --hot 8

``run`` 會：把 ``--db`` 複製到暫存資料目錄（不改原檔）、用 ``MCT_DATA_DIR`` 指向該目錄、
啟動真實 App（``main.bootstrap_runtime()`` + ``ft.run(main.main, export_asgi_app=True)`` + uvicorn），
再用 Playwright 開啟並逐項操作。每個操作記錄兩種延遲：

- ``server``：伺服器端「收到前端事件 → 最後一次送出 UI patch」（以 patch 靜默 ``--quiet-ms`` 判定結束，
  背景工作完成後的更新也算在內）。
- ``e2e``：瀏覽器端「動作 → 最後一次畫面 semantics 變動」。CanvasKit 沒有 DOM，量測工具會開啟
  semantics 樹並用 MutationObserver 記錄最後變動時間。

另外 ``server`` 端會監看事件迴圈卡頓（``stall``）：事件迴圈被同步工作擋住超過 ``--stall-ms`` 時，
取樣主執行緒的呼叫堆疊，方便找出 UI 執行緒上做 DB/IO 的位置。
每項操作冷 1 次 + 熱 ``--hot`` 次，輸出中位數與 p95。

這是開發輔助工具，不屬於 App，不會被 pytest 執行。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# 伺服器端：包裝 Flet 的事件分派與訊息送出
# ---------------------------------------------------------------------------


class ServerProbe:
    """記錄事件收到 / patch 送出 / 事件迴圈卡頓；由另一個 HTTP 埠提供給量測端讀取。"""

    def __init__(self, stall_ms: float) -> None:
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.stall_ms = stall_ms
        self.main_thread_id = threading.get_ident()
        self.heartbeat = time.perf_counter()
        self.sampled_for = 0.0

    def add(self, kind: str, **data) -> None:
        with self.lock:
            self.events.append({"t": time.perf_counter(), "kind": kind, **data})

    def since(self, index: int) -> tuple[int, list[dict]]:
        with self.lock:
            return len(self.events), self.events[index:]

    # -- 卡頓取樣：另一條執行緒觀察事件迴圈 heartbeat -------------------------
    def start_watchdog(self) -> None:
        def run() -> None:
            sampling_since: float | None = None
            frames_seen: list[str] = []
            while True:
                time.sleep(0.004)
                age = (time.perf_counter() - self.heartbeat) * 1000
                if age > self.stall_ms:
                    if sampling_since is None:
                        sampling_since = self.heartbeat
                        frames_seen = []
                    frame = sys._current_frames().get(self.main_thread_id)
                    stack = _app_stack(frame)
                    if stack and (not frames_seen or frames_seen[-1] != stack):
                        frames_seen.append(stack)
                elif sampling_since is not None:
                    self.add(
                        "stall",
                        ms=round((time.perf_counter() - sampling_since) * 1000, 1),
                        stacks=frames_seen[-4:],
                    )
                    sampling_since = None

        threading.Thread(target=run, daemon=True, name="perf-watchdog").start()

    async def heartbeat_task(self) -> None:
        import asyncio

        while True:
            self.heartbeat = time.perf_counter()
            await asyncio.sleep(0.002)


def _app_stack(frame) -> str:
    """最內層 3 個框架（不限檔案，可看出卡在 Flet 或 sqlite）+ 往外的 App 框架。"""
    inner: list[str] = []
    parts: list[str] = []
    depth = 0
    while frame is not None:
        path = frame.f_code.co_filename
        entry = f"{Path(path).name}:{frame.f_lineno}:{frame.f_code.co_name}"
        if depth < 3:
            inner.append(entry)
        elif str(ROOT) in path and "/tools/" not in path:
            parts.append(entry)
        depth += 1
        frame = frame.f_back
    return " < ".join(inner + ["|"] + parts[:6])


def _install_hooks(probe: ServerProbe) -> None:
    import flet_web.fastapi.flet_app as fapp
    from flet.messaging.session import Session

    original_dispatch = Session.dispatch_event

    async def dispatch_event(self, control_id, event_name, event_data):
        control = None
        try:
            control = self._Session__index.get(control_id)
        except Exception:  # noqa: BLE001 - 量測工具，不影響被測程式
            control = None
        label = type(control).__name__ if control is not None else "?"
        probe.add("recv", control=label, name=event_name)
        started = time.perf_counter()
        try:
            return await original_dispatch(self, control_id, event_name, event_data)
        finally:
            probe.add(
                "handled",
                control=label,
                name=event_name,
                ms=round((time.perf_counter() - started) * 1000, 1),
            )

    Session.dispatch_event = dispatch_event

    original_patch_control = Session.patch_control

    def patch_control(self, control, *args, **kwargs):
        """記錄每次 ``update()`` 的控制項型別與呼叫位置（找出多餘或重複的更新）。"""
        frame = sys._getframe(1)
        callers: list[str] = []
        while frame is not None and len(callers) < 3:
            path = frame.f_code.co_filename
            if str(ROOT / "app") in path:
                callers.append(
                    f"{Path(path).name}:{frame.f_lineno}:{frame.f_code.co_name}"
                )
            frame = frame.f_back
        started = time.perf_counter()
        try:
            return original_patch_control(self, control, *args, **kwargs)
        finally:
            probe.add(
                "patch",
                control=type(control).__name__,
                ms=round((time.perf_counter() - started) * 1000, 1),
                callers=callers,
            )

    Session.patch_control = patch_control

    original_send = fapp.FletApp.send_message
    original_packb = fapp.msgpack.packb
    size_box = {"n": 0}

    def packb(*args, **kwargs):
        data = original_packb(*args, **kwargs)
        size_box["n"] = len(data)
        return data

    fapp.msgpack.packb = packb

    def send_message(self, message):
        original_send(self, message)
        probe.add(
            "send", action=str(getattr(message, "action", "?")), bytes=size_box["n"]
        )

    fapp.FletApp.send_message = send_message
    _install_db_hooks(probe)


def _install_db_hooks(probe: ServerProbe) -> None:
    """記錄 ``TranslationDB`` 公開方法的耗時與執行緒（區分畫面執行緒與 worker）。"""
    import functools
    import inspect

    from translation_tool.translation_db import TranslationDB

    def wrap(name, func):
        @functools.wraps(func)
        def timed(self, *args, **kwargs):
            started = time.perf_counter()
            try:
                return func(self, *args, **kwargs)
            finally:
                ms = (time.perf_counter() - started) * 1000
                if ms >= 3:
                    probe.add(
                        "db",
                        name=name,
                        ms=round(ms, 1),
                        ui=threading.get_ident() == probe.main_thread_id,
                    )

        return timed

    for name, func in list(vars(TranslationDB).items()):
        if name.startswith("_") or not inspect.isfunction(func):
            continue
        setattr(TranslationDB, name, wrap(name, func))


class LoopProfiler:
    """對事件迴圈執行緒做 cProfile；由 HTTP 端點啟停。"""

    def __init__(self) -> None:
        import cProfile

        self.loop = None
        self.profile = cProfile.Profile()
        self.done = threading.Event()

    def start(self) -> None:
        import cProfile

        self.profile = cProfile.Profile()
        self.loop.call_soon_threadsafe(self.profile.enable)

    def stop(self, limit: int = 25) -> str:
        import io
        import pstats

        self.done.clear()

        def disable() -> None:
            self.profile.disable()
            self.done.set()

        self.loop.call_soon_threadsafe(disable)
        self.done.wait(5)
        buffer = io.StringIO()
        stats = pstats.Stats(self.profile, stream=buffer)
        stats.sort_stats("cumulative").print_stats(limit)
        return buffer.getvalue()


PROFILER = LoopProfiler()


def _probe_http_server(probe: ServerProbe, port: int) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/prof/start"):
                PROFILER.start()
                self._reply({"ok": True})
                return
            if self.path.startswith("/prof/stop"):
                self._reply({"text": PROFILER.stop()})
                return
            query = parse_qs(urlparse(self.path).query)
            index = int((query.get("since") or ["0"])[0])
            total, events = probe.since(index)
            self._reply({"next": total, "events": events, "now": time.perf_counter()})

        def _reply(self, payload) -> None:
            body = json.dumps(payload)
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("access-control-allow-origin", "*")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def serve(args: argparse.Namespace) -> None:
    """啟動被量測的真實 App（MCT_DATA_DIR 已由 ``run`` 設好）。"""
    import asyncio

    import flet as ft
    import uvicorn

    import main as app_main
    from translation_tool.utils.app_paths import get_resource_root

    probe = ServerProbe(args.stall_ms)
    _install_hooks(probe)
    _probe_http_server(probe, args.probe_port)
    app_main.bootstrap_runtime()
    app = ft.run(
        app_main.main,
        export_asgi_app=True,
        assets_dir=str(get_resource_root() / "assets"),
        web_renderer=ft.WebRenderer.CANVAS_KIT,
    )

    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
    server = uvicorn.Server(config)

    async def go() -> None:
        probe.main_thread_id = threading.get_ident()
        PROFILER.loop = asyncio.get_running_loop()
        probe.start_watchdog()
        asyncio.get_running_loop().create_task(probe.heartbeat_task())
        await server.serve()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# 量測端
# ---------------------------------------------------------------------------


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def summarize(samples: list[dict]) -> dict:
    """samples[0] 為冷、其餘為熱。"""
    if not samples:
        return {}
    hot = samples[1:]
    result: dict = {"cold": samples[0]}
    for field in ("server_ms", "first_patch_ms", "e2e_ms", "loop_block_ms"):
        values = [s[field] for s in hot if s.get(field) is not None]
        if values:
            result[f"hot_{field}_median"] = round(statistics.median(values), 1)
            result[f"hot_{field}_p95"] = round(percentile(values, 95), 1)
    if hot and hot[-1].get("db_ms"):
        result["db_last_hot"] = hot[-1]["db_ms"]
    return result


SEMANTICS_JS = """
() => {
  if (window.__perf) return;
  const state = {last: 0, count: 0};
  window.__perf = state;
  const host = document.querySelector('flt-semantics-host') || document.body;
  const obs = new MutationObserver((records) => {
    state.last = performance.now(); state.count += records.length;
  });
  obs.observe(host, {subtree: true, childList: true, characterData: true, attributes: true});
}
"""


class Driver:
    """包住 Playwright 頁面與伺服器 probe；提供 ``measure(name, action)``。"""

    def __init__(self, page, probe_port: int, quiet_ms: int, timeout_ms: int) -> None:
        self.page = page
        self.probe_url = f"http://127.0.0.1:{probe_port}/"
        self.quiet_ms = quiet_ms
        self.timeout_ms = timeout_ms
        self.cursor = 0
        self.results: dict[str, list[dict]] = {}
        self.notes: dict[str, str] = {}
        self.stalls: dict[str, list[dict]] = {}

    # -- 伺服器 probe -------------------------------------------------------
    def _fetch(self) -> list[dict]:
        import urllib.request

        with urllib.request.urlopen(f"{self.probe_url}?since={self.cursor}") as resp:
            data = json.loads(resp.read())
        self.cursor = data["next"]
        return data["events"]

    def drain(self) -> None:
        self._fetch()

    def enable_semantics(self) -> None:
        self.page.evaluate(
            "() => { const p = document.querySelector('flt-semantics-placeholder');"
            " if (p) p.click(); }"
        )
        self.page.wait_for_timeout(500)
        self.page.evaluate(SEMANTICS_JS)

    def settle(self, ms: int | None = None) -> None:
        """等伺服器 patch 靜默（用於準備步驟，不計入量測）。"""
        self.wait_quiet(ms or self.quiet_ms)
        self.drain()

    def wait_quiet(self, quiet_ms: int) -> None:
        deadline = time.perf_counter() + self.timeout_ms / 1000
        events: list[dict] = []
        last_activity = time.perf_counter()
        while time.perf_counter() < deadline:
            time.sleep(0.02)
            new = self._fetch()
            if new:
                events.extend(new)
                if any(e["kind"] in ("send", "recv", "handled") for e in new):
                    last_activity = time.perf_counter()
            if (time.perf_counter() - last_activity) * 1000 >= quiet_ms:
                break
        self._pending = events

    def profile_start(self) -> None:
        import urllib.request

        urllib.request.urlopen(self.probe_url + "prof/start").read()

    def profile_stop(self) -> str:
        import urllib.request

        with urllib.request.urlopen(self.probe_url + "prof/stop") as resp:
            return json.loads(resp.read())["text"]

    def measure(self, name: str, action, *, label: str = "") -> dict:
        """執行 ``action()``（會觸發一次前端操作），回傳 server/e2e 延遲。"""
        self.drain()
        self.page.evaluate(
            "() => { if (window.__perf) { window.__perf.last = 0; window.__perf.t0 = 0; } }"
        )
        t0 = time.perf_counter()
        action()
        t_action_done = time.perf_counter()
        events: list[dict] = []
        last_activity = time.perf_counter()
        deadline = t0 + self.timeout_ms / 1000
        while time.perf_counter() < deadline:
            time.sleep(0.01)
            new = self._fetch()
            if new:
                events.extend(new)
                if any(e["kind"] in ("send", "recv") for e in new):
                    last_activity = time.perf_counter()
            # 背景工作完成後的更新也要算：靜默 quiet_ms 才算結束
            if (time.perf_counter() - last_activity) * 1000 >= self.quiet_ms and any(
                e["kind"] == "recv" for e in events
            ):
                break
            if (
                not any(e["kind"] == "recv" for e in events)
                and (time.perf_counter() - t0) * 1000 > 1500
            ):
                break
        recvs = [e for e in events if e["kind"] == "recv"]
        sends = [e for e in events if e["kind"] == "send"]
        server_ms = None
        first_ms = None
        if recvs and sends:
            first_recv = recvs[0]["t"]
            # 外殼每隔幾秒的 API Key 輪詢也會 patch；它與這次操作無關，不計入
            patches = [
                e
                for e in events
                if e["kind"] == "patch"
                and not any("_poll_keys" in c for c in e["callers"])
            ]
            last_send = max([e["t"] for e in patches] or [s["t"] for s in sends])
            server_ms = round((last_send - first_recv) * 1000, 1)
            first_ms = round((min(s["t"] for s in sends) - first_recv) * 1000, 1)
        db_calls = [e for e in events if e["kind"] == "db"]
        db_summary: dict[str, float] = {}
        for call in db_calls:
            key = call["name"] + ("@ui" if call["ui"] else "")
            db_summary[key] = round(db_summary.get(key, 0.0) + call["ms"], 1)
        db_top = dict(sorted(db_summary.items(), key=lambda kv: -kv[1])[:6])
        sem = self.page.evaluate(
            "() => window.__perf ? [window.__perf.t0, window.__perf.last] : [0, 0]"
        )
        e2e_ms = round(sem[1] - sem[0], 1) if sem[0] and sem[1] >= sem[0] else None
        stalls = [e for e in events if e["kind"] == "stall"]
        origin = recvs[0]["t"] if recvs else t0
        timeline = [
            [
                e["kind"],
                round((e["t"] - origin) * 1000),
                e.get("name") or e.get("action") or e.get("control") or "",
                e.get("ms") or e.get("bytes") or 0,
                " < ".join(e.get("callers", []))
                if e["kind"] == "patch"
                else ("ui" if e.get("ui") else ""),
            ]
            for e in events
            if e["kind"] in ("recv", "send", "db", "patch")
        ][:120]
        sample = {
            "label": label,
            "timeline": timeline,
            "server_ms": server_ms,
            "first_patch_ms": first_ms,
            "db_ms": db_top,
            "e2e_ms": e2e_ms,
            "action_ms": round((t_action_done - t0) * 1000, 1),
            "events": [f"{e['control']}.{e['name']}" for e in recvs][:4],
            "patches": len(sends),
            "patch_kb": round(sum(s.get("bytes", 0) for s in sends) / 1024, 1),
            "loop_block_ms": round(max((s["ms"] for s in stalls), default=0.0), 1),
        }
        if stalls:
            self.stalls.setdefault(name, []).extend(stalls)
        self.results.setdefault(name, []).append(sample)
        return sample


# -- 頁面操作 helper --------------------------------------------------------


def _node(page, role: str, name: str, *, exact: bool = False, nth: int = 0):
    return page.get_by_role(role, name=name, exact=exact).nth(nth)


def click_text(page, text: str, *, role: str | None = None, nth: int = 0) -> None:
    if role:
        loc = page.get_by_role(role, name=text).nth(nth)
    else:
        loc = page.locator(f'flt-semantics:has-text("{text}")').nth(nth)
    loc.click(timeout=5000)


def prepare_environment(args: argparse.Namespace, work: Path) -> dict:
    """建立資料目錄與 config.json，回傳 env。"""
    data_dir = work / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "moddb.db"
    if not args.no_copy:
        shutil.copyfile(args.db, db_path)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(args.db) + suffix)
            if sidecar.exists():
                shutil.copyfile(sidecar, Path(str(db_path) + suffix))
    else:
        db_path = Path(args.db)
    config = {"translation_db": {"enabled": True, "path": str(db_path)}}
    (data_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    env = dict(os.environ)
    env["MCT_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http(url: str, timeout: float = 60) -> None:
    import urllib.request

    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2).read(1)
            return
        except Exception:  # noqa: BLE001 - 輪詢啟動
            time.sleep(0.3)
    raise SystemExit(f"伺服器未在 {timeout}s 內啟動：{url}")


def run(args: argparse.Namespace) -> None:
    from tools import perf_moddb_web_scenarios as scenarios

    work = Path(args.work or tempfile.mkdtemp(prefix="perf-moddb-"))
    work.mkdir(parents=True, exist_ok=True)
    env = prepare_environment(args, work)
    port, probe_port = free_port(), free_port()
    server_log = open(work / "server.log", "w", encoding="utf-8")  # noqa: SIM115
    server = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "serve",
            "--port",
            str(port),
            "--probe-port",
            str(probe_port),
            "--stall-ms",
            str(args.stall_ms),
        ],
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
    )
    try:
        wait_http(f"http://127.0.0.1:{port}/")
        wait_http(f"http://127.0.0.1:{probe_port}/")
        output = scenarios.run_all(args, port, probe_port, work, Driver)
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
    out = {"db": str(args.db), "work": str(work), "results": output}
    if args.out:
        Path(args.out).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    scenarios.print_table(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    runp = sub.add_parser("run", help="啟動 App 並量測")
    runp.add_argument(
        "--db", required=True, help="要量測的 Mod 資料庫（會複製，不改原檔）"
    )
    runp.add_argument(
        "--no-copy", action="store_true", help="直接使用 --db（會寫入！）"
    )
    runp.add_argument("--work", help="暫存工作目錄（預設自動建立）")
    runp.add_argument("--out", help="結果 JSON 輸出路徑")
    runp.add_argument("--only", default="", help="只跑指定群組，逗號分隔")
    runp.add_argument("--hot", type=int, default=5, help="每項熱次數（預設 5）")
    runp.add_argument("--quiet-ms", type=int, default=250, help="patch 靜默多久算完成")
    runp.add_argument("--timeout-ms", type=int, default=20000)
    runp.add_argument("--stall-ms", type=float, default=40.0)
    runp.add_argument("--viewport", default="1440x1100")
    runp.add_argument(
        "--scan-source", help="含 jar 的資料夾；掃描情境會複製其中前 3 個 jar"
    )
    runp.add_argument(
        "--profile", action="store_true", help="每項多做一次並輸出事件迴圈 cProfile"
    )
    runp.add_argument("--screenshots", help="失敗與關鍵步驟截圖資料夾")
    servep = sub.add_parser("serve", help="（內部）啟動被量測的 App")
    servep.add_argument("--port", type=int, required=True)
    servep.add_argument("--probe-port", type=int, required=True)
    servep.add_argument("--stall-ms", type=float, default=40.0)
    args = parser.parse_args()
    if args.cmd == "serve":
        serve(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
