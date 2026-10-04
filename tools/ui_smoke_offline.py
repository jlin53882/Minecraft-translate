"""在封鎖外網的環境（例如沙箱、CI）執行 ``tools/ui_smoke.py``。

Flet 網頁版會從 ``www.gstatic.com`` 下載 CanvasKit 與字型；外網被擋時頁面標題會一直停在 ``Flet``，
smoke 以「等待 smoke 標題逾時」失敗。這個包裝把那些請求導到本機檔案：

- CanvasKit：已安裝的 ``flet_web`` 套件內附的 ``canvaskit/``。
- 字型：優先用環境變數 ``MCT_SMOKE_LATIN_FONT`` / ``MCT_SMOKE_CJK_FONT`` 指定的字型檔，
  找不到時用常見的系統字型；沒有任何 CJK 字型時中文會顯示成方塊（版面仍可比對）。

用法與 ``ui_smoke.py`` 相同（參數原樣轉交），例如：

    uv run python tools/ui_smoke_offline.py --browser-executable /path/to/chrome \\
        --theme dark --viewport 1360x900 --output-dir .artifacts/ui-smoke/offline

它只是輔助工具，不改變 ``ui_smoke.py`` 的行為；有網路時請直接用 ``ui_smoke.py``。
注意：被擋的其他外部請求仍會讓 ``report.json`` 的 ``console_errors`` 非空（``ok`` 為 false），
截圖本身是有效的；判讀以截圖與 ``missing_cases`` / ``page_errors`` 為準。
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import Browser, BrowserType  # noqa: E402

_LATIN_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
_CJK_CANDIDATES = (
    "/etc/alternatives/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msjh.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


def _first_existing(env_name: str, candidates: tuple[str, ...]) -> Path | None:
    explicit = os.environ.get(env_name)
    for raw in (explicit, *candidates):
        if raw and Path(raw).is_file():
            return Path(raw)
    return None


def _canvaskit_dir() -> Path:
    spec = importlib.util.find_spec("flet_web")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("找不到 flet_web 套件（需要它內附的 canvaskit）")
    path = Path(next(iter(spec.submodule_search_locations))) / "web" / "canvaskit"
    if not path.is_dir():
        raise SystemExit(f"找不到 CanvasKit：{path}")
    return path


CANVASKIT = _canvaskit_dir()
LATIN_FONT = _first_existing("MCT_SMOKE_LATIN_FONT", _LATIN_CANDIDATES)
CJK_FONT = _first_existing("MCT_SMOKE_CJK_FONT", _CJK_CANDIDATES) or LATIN_FONT


def _serve_canvaskit(route) -> None:
    match = re.search(r"flutter-canvaskit/[0-9a-f]+/(.*)$", route.request.url)
    target = CANVASKIT / match.group(1) if match else None
    if target is not None and target.is_file():
        content_type = (
            "application/wasm" if target.suffix == ".wasm" else "application/javascript"
        )
        route.fulfill(
            body=target.read_bytes(),
            content_type=content_type,
            headers={"access-control-allow-origin": "*"},
        )
    else:
        route.abort()


def _serve_font(route) -> None:
    font = LATIN_FONT if "roboto" in route.request.url.lower() else CJK_FONT
    if font is None:
        route.abort()
        return
    route.fulfill(
        body=font.read_bytes(),
        content_type="font/ttf",
        headers={"access-control-allow-origin": "*"},
    )


_original_new_context = Browser.new_context
_original_launch = BrowserType.launch


def _new_context(self, *args, **kwargs):
    context = _original_new_context(self, *args, **kwargs)
    context.route("**/flutter-canvaskit/**", _serve_canvaskit)
    context.route("https://fonts.gstatic.com/**", _serve_font)
    return context


def _launch(self, *args, **kwargs):
    # 以 root 執行的容器需要 --no-sandbox
    kwargs["args"] = [*list(kwargs.get("args") or []), "--no-sandbox"]
    return _original_launch(self, *args, **kwargs)


Browser.new_context = _new_context
BrowserType.launch = _launch

if __name__ == "__main__":
    from tools import ui_smoke

    raise SystemExit(ui_smoke.main())
