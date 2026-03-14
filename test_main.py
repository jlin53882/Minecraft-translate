"""test_main.py（Web 測試入口）

用途：
- 使用 Web 模式測試 Flet UI
- 可用瀏覽器開發者工具檢查元素
- 適合修改 UI 時快速驗證

使用方法：
    python test_main.py

然後用瀏覽器打開 http://localhost:8550
"""

import flet as ft
import sys


def main(page: ft.Page):
    """Web 模式入口，包裝原本的 main 邏輯"""
    # 直接呼叫原本的 main.py，不做任何修改
    from main import main as original_main
    original_main(page)


if __name__ == "__main__":
    # 先初始化 runtime
    from main import bootstrap_runtime

    try:
        bootstrap_runtime()
    except Exception as e:
        print(f"配置或日誌系統初始化失敗: {e}")
        sys.exit(1)

    print("=" * 50)
    print("Web 測試模式啟動中...")
    print("請用瀏覽器打開: http://localhost:8550")
    print("按 Ctrl+C 停止服務")
    print("=" * 50)

    # Web 模式運行
    ft.app(
        target=main,
        host="0.0.0.0",
        port=8550,
        view=ft.AppView.WEB_BROWSER,
    )
