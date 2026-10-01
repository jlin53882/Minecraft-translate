"""main.py（Flet App 入口）

責任：
- 只在 `__main__` 路徑呼叫 bootstrap_runtime() 做一次性的 runtime 初始化。
- 把 Page 交給 ``app.shell.AppShell``（側欄 / 頂列 / 狀態列 / 快速跳轉 / 切頁）。

維護注意：
- main.py 可能被測試 import；因此不能在 import 階段就做 logging/config 初始化。
- 快取搜尋索引重建會在啟動後用背景 thread 執行，避免主畫面卡住。
- 新增頁面請改 ``app/view_registry.py`` 的 ``VIEW_SPECS``，不用動這裡。
"""

import logging
from pathlib import Path

import flet as ft

from app.shell import AppShell
from app.startup_tasks import start_background_startup_tasks
from app.view_registry import DEFAULT_WINDOW_SIZE, MIN_WINDOW_SIZE

# 視窗尺寸常數（實際值定義在 view_registry；保留這些名稱給既有的測試 / 外部引用）
WINDOW_WIDTH_DEFAULT, WINDOW_HEIGHT_DEFAULT = DEFAULT_WINDOW_SIZE
WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT = MIN_WINDOW_SIZE

logger = logging.getLogger("main_app")


def bootstrap_runtime():
    """
    初始化 runtime（config + logging），只應在 script entry 被呼叫一次。

    流程：
    1. load_config()      → 讀取並合併 default / example / user 三層設定
    2. setup_logging()    → 根據 config 設定日誌等級、輸出格式、檔案路徑
    3. 驗證日誌系統是否成功初始化（取根 logger 的 effective level 確認）

    注意：main.py 可被測試環境 import，因此不在模組層執行此初始化，
    而是延後到 `if __name__ == "__main__"` 才呼叫，確保測試自行控制 runtime。
    """
    from translation_tool.utils.config_manager import load_config, setup_logging

    config = load_config()
    setup_logging(config)

    root_level = logging.getLogger().getEffectiveLevel()
    logger.info(
        f"日誌系統初始化成功，根記錄器級別已設為 {logging.getLevelName(root_level)} ({root_level})。"
    )


def main(page: ft.Page):
    """Flet 應用程式的 entry point，由 ft.run(main) 觸發。"""
    AppShell(page).mount()
    # 背景啟動任務（索引重建等不影響啟動速度的慢工作）
    start_background_startup_tasks()


if __name__ == "__main__":
    """
    腳本直接執行時才初始化 runtime 並啟動 Flet App。
    測試環境 import 此檔案時不會觸發這段邏輯。
    """
    try:
        bootstrap_runtime()
    except Exception as e:  # noqa: BLE001
        # bootstrap 失敗可能是 config 格式錯誤或 logging 初始化失敗，
        # 印出訊息後仍嘗試啟動（讓使用者能看到 GUI 介面）
        print(f"致命錯誤：配置或日誌系統初始化失敗！錯誤: {e}")

    ft.run(main, assets_dir=str(Path(__file__).resolve().parent / "assets"))
