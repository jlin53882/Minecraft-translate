"""app/config_store.py：設定的單一讀寫入口（讀取、局部寫入、異動通知）。

為什麼需要它
- 設定頁、合併頁與外殼都會寫 ``config.json``；外殼（API Key 狀態、模型名稱、主題）
  必須在「任何地方存檔後」立刻更新，不能靠重開 App 或各頁互相呼叫。
- ``config_service._save_app_config()`` 預設存檔成功後會呼叫 :func:`notify_saved`，
  因此舊頁面照原本方式存檔也會通知訂閱者。
- 通知契約：**所有訂閱者 callback 都在寫入鎖釋放後才執行**（``save`` / ``set_value`` 皆然）。
- 寫入所有權：app 層對 ``config.json`` 的寫入都在 :func:`write_lock` 內進行（含舊的 service 路徑）。

兩種寫入
- :func:`set_value`：只改**使用者檔**（``config.json``）裡的一個欄位，不會把三層合併後的預設值
  固化進使用者檔（外殼偏好用，例如切換主題）。
- :func:`save`：整份設定存檔（設定頁用，含 normalization）。
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_listeners: list[Callable[[], None]] = []
_lock = threading.Lock()
_write_lock = threading.Lock()  # 序列化對 config.json 的 read-modify-write

_MISSING = object()


def write_lock() -> threading.Lock:
    """序列化對 ``config.json`` 寫入的鎖。所有 app 層的寫入（``set_value`` / ``save`` /
    ``config_service.save_config_json``）都必須在這把鎖內寫檔、鎖外通知。"""
    return _write_lock


def subscribe(callback: Callable[[], None]) -> Callable[[], None]:
    """訂閱設定異動；回傳取消訂閱的函式。callback 在存檔的執行緒被呼叫。"""
    with _lock:
        _listeners.append(callback)

    def unsubscribe() -> None:
        with _lock:
            if callback in _listeners:
                _listeners.remove(callback)

    return unsubscribe


def notify_saved() -> None:
    """設定已寫入：通知所有訂閱者（單一訂閱者出錯不影響其他人）。"""
    with _lock:
        listeners = list(_listeners)
    for callback in listeners:
        try:
            callback()
        except Exception:
            logger.exception("設定異動訂閱者失敗")


def _walk(data: Any, path: str, default: Any = None) -> Any:
    node = data
    for key in path.split("."):
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return default
    return node


def _config_path() -> str:
    """設定檔路徑的單一來源（測試改 ``config_service.CONFIG_PATH`` 就能整個換掉）。"""
    from app.services_impl.config_service import CONFIG_PATH

    return CONFIG_PATH


def get(path: str, default: Any = None) -> Any:
    """讀取三層合併後的設定值，例如 ``get("lm_translator.temperature", 0.3)``。

    回傳值可能是設定快取內的物件（dict / list）：請當成唯讀；要修改請用 :func:`snapshot`。
    """
    from translation_tool.utils.config_manager import load_config_shared

    return _walk(load_config_shared(_config_path()), path, default)


def snapshot() -> dict:
    """三層合併後的設定複本（可自由修改）。"""
    from translation_tool.utils.config_manager import load_config

    return load_config(_config_path())


def set_value(path: str, value: Any) -> bool:
    """只把使用者檔裡 ``path`` 這個欄位改成 ``value``，其餘不動；成功後通知訂閱者。

    使用者檔不存在時建立；檔案內容無法解析時**不覆寫**（避免毀掉使用者手改的壞檔），回傳 False。
    """
    from translation_tool.utils.config_manager import resolve_project_path, save_config

    keys = path.split(".")
    config_path = _config_path()
    with _write_lock:
        target = resolve_project_path(config_path)
        raw: dict = {}
        if target.exists():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("config.json 無法解析，略過寫入 %s", path)
                return False
            if not isinstance(loaded, dict):
                return False
            raw = loaded
        node = raw
        for key in keys[:-1]:
            child = node.get(key)
            if not isinstance(child, dict):
                child = {}
                node[key] = child
            node = child
        node[keys[-1]] = copy.deepcopy(value)
        ok = bool(save_config(raw, config_path))
    if ok:
        notify_saved()
    return ok


def save(config: dict) -> bool:
    """整份設定存檔（含 normalization）；成功後通知訂閱者一次。

    寫入與通知的鎖邊界由 ``config_service._save_app_config`` 負責（所有整份存檔的路徑，
    包含舊頁面直接呼叫的 ``save_config_json``，都走同一個入口）：寫入在 ``write_lock()`` 內完成，
    通知在鎖**釋放後**才進行，訂閱者 callback 再呼叫 ``set_value`` / ``save`` 不會重入死鎖。
    """
    from app.services_impl.config_service import _save_app_config

    return bool(_save_app_config(config))


# -- 外殼偏好 -----------------------------------------------------------------

THEME_MODES = ("dark", "light")
DEFAULT_THEME_MODE = "dark"


def get_theme_mode() -> str:
    """使用者偏好的主題；設定值不合法時回傳預設（深色）。"""
    value = get("ui.theme_mode", DEFAULT_THEME_MODE)
    return value if value in THEME_MODES else DEFAULT_THEME_MODE


def set_theme_mode(mode: str) -> bool:
    if mode not in THEME_MODES:
        raise ValueError(f"未知的主題：{mode}")
    return set_value("ui.theme_mode", mode)
