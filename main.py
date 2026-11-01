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
import multiprocessing
import os
import socket

import flet as ft

from app.shell import AppShell
from app.startup_tasks import start_background_startup_tasks
from app.view_registry import DEFAULT_WINDOW_SIZE, MIN_WINDOW_SIZE
from translation_tool.utils.app_paths import get_resource_root

# 視窗尺寸常數（實際值定義在 view_registry；保留這些名稱給既有的測試 / 外部引用）
WINDOW_WIDTH_DEFAULT, WINDOW_HEIGHT_DEFAULT = DEFAULT_WINDOW_SIZE
WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT = MIN_WINDOW_SIZE

logger = logging.getLogger("main_app")


def _port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """嘗試綁定連接埠；能綁定代表目前沒有其他程式占用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def resolve_server_port(preferred: int | None, host: str = "127.0.0.1") -> int:
    """回傳 Flet 網頁服務可用的連接埠。

    偏好埠（``FLET_SERVER_PORT`` 或 Flet 預設的 8000）被占用時，通常是上一個
    沒有關閉的程式仍在執行；此時改用系統配置的空閒埠，並留下警告，避免 uvicorn
    啟動失敗後 Flet 關閉流程再拋出第二個錯誤。
    """
    if preferred is not None and _port_is_free(preferred, host):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        free_port = probe.getsockname()[1]
    if preferred is not None:
        logger.warning(
            "連接埠 %d 已被占用（可能是上一個程式尚未關閉），改用空閒連接埠 %d",
            preferred,
            free_port,
        )
    return free_port


def _preferred_server_port() -> int | None:
    """讀取 ``FLET_SERVER_PORT``；未設定或格式錯誤時回傳 None。"""
    raw = os.getenv("FLET_SERVER_PORT", "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError:
        logger.warning("FLET_SERVER_PORT 不是有效的連接埠數字：%r，忽略此設定", raw)
        return None
    return port if 0 < port < 65536 else None


def _web_server_port() -> int:
    """網頁服務模式才需要檢查埠；桌面模式維持 Flet 自動選埠（0）。

    ``FLET_SERVER_PORT`` 有設定時一定使用它；網頁服務強制啟用但沒有設定時，
    Flet 預設使用 8000，這裡一併檢查。
    """
    preferred = _preferred_server_port()
    forced_web = os.getenv("FLET_FORCE_WEB_SERVER", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if preferred is None and forced_web:
        preferred = 8000
    if preferred is None:
        return 0
    return resolve_server_port(preferred)


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
    from translation_tool.utils.config_manager import (
        load_config,
        setup_logging,
        sync_missing_config_keys,
    )

    # 範本新增的設定欄位補進使用者的 config.json（只補缺少的 key，不改使用者的值）
    try:
        sync_missing_config_keys()
    except Exception:
        logger.warning("同步新設定欄位失敗，不影響啟動", exc_info=True)

    config = load_config()
    setup_logging(config)

    # log_level / log_format 存檔後立即套用（不必等下次啟動流水線）
    from app import config_store
    from app.services_impl.config_service import _load_app_config
    from app.services_impl.logging_service import watch_logging_config

    watch_logging_config(config_store.subscribe_paths, _load_app_config)

    root_level = logging.getLogger().getEffectiveLevel()
    logger.info(
        f"日誌系統初始化成功，根記錄器級別已設為 {logging.getLevelName(root_level)} ({root_level})。"
    )


def main(page: ft.Page):
    """Flet 應用程式的 entry point，由 ft.run(main) 觸發。"""
    shell = AppShell(page)
    shell.mount()
    # 背景啟動任務（索引重建等不影響啟動速度的慢工作）
    start_background_startup_tasks(shell.operations)


if __name__ == "__main__":
    """
    腳本直接執行時才初始化 runtime 並啟動 Flet App。
    測試環境 import 此檔案時不會觸發這段邏輯。
    """
    # 打包後的程式以同一個 exe 啟動掃描 worker 行程；沒有這行 worker 會再開一個視窗
    multiprocessing.freeze_support()
    try:
        bootstrap_runtime()
    except Exception as e:
        # bootstrap 失敗可能是 config 格式錯誤或 logging 初始化失敗，
        # 印出訊息後仍嘗試啟動（讓使用者能看到 GUI 介面）
        # 此時日誌可能尚未初始化；root logger 的預設 handler 仍會把 CRITICAL 印到 stderr
        logger.critical(
            "致命錯誤：配置或日誌系統初始化失敗！錯誤: %s", e, exc_info=True
        )

    server_port = _web_server_port()
    if server_port:
        # Flet 會以 FLET_SERVER_PORT 覆蓋 port 參數，因此選定的埠必須寫回環境變數。
        os.environ["FLET_SERVER_PORT"] = str(server_port)
    ft.run(
        main,
        assets_dir=str(get_resource_root() / "assets"),
        port=server_port,
    )
