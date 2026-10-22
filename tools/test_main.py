"""test_main.py（Web 測試入口）

用途：
- 使用 Web 模式測試 Flet UI
- 可用瀏覽器開發者工具檢查元素
- 適合修改 UI 時快速驗證

使用方法（在專案根目錄）：
    python tools/test_main.py [--host 127.0.0.1] [--port 8550]

然後用瀏覽器打開 http://localhost:8550
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="以 Web 模式啟動 Minecraft-translate 的 Flet UI（開發用）。"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="綁定位址（預設 127.0.0.1）"
    )
    parser.add_argument("--port", type=int, default=8550, help="連接埠（預設 8550）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # 直接執行 tools/test_main.py 時，sys.path[0] 是 tools/，需手動加入專案根目錄
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import flet as ft

    from main import bootstrap_runtime
    from main import main as app_main

    try:
        bootstrap_runtime()
    except Exception as e:  # noqa: BLE001  # 開發入口：任何初始化失敗都要印出原因後結束
        print(f"配置或日誌系統初始化失敗: {e}")
        return 1

    print("=" * 50)
    print("Web 測試模式啟動中...")
    print(f"請用瀏覽器打開: http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止服務")
    print("=" * 50)

    # Flet 1.x 以 ft.run 啟動；assets 路徑與 main.py 一致
    ft.run(
        app_main,
        host=args.host,
        port=args.port,
        view=ft.AppView.WEB_BROWSER,
        assets_dir=str(ROOT / "assets"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
