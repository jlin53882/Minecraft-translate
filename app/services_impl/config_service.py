"""app.services_impl.config_service

本模組承接 `app/services.py` 原本的 config / replace_rules 相關 IO 與路徑常數。

PR15 範圍（只抽離 IO/wrapper，不改 pipeline/service 流程）：
- `PROJECT_ROOT / CONFIG_PATH / REPLACE_RULES_PATH`
- `_load_app_config()` / `_save_app_config()`
- `load_config_json()` / `save_config_json()`
- `load_replace_rules()` / `save_replace_rules()`

維護注意：
- `PROJECT_ROOT` 必須使用 `Path(__file__).resolve().parents[2]`（PR15 約定）。
- 本模組不應 import `logging_service`（依賴方向固定由 services.py 統整）。
- 對外 API 需維持與舊版 `app.services` 同名，讓 services.py 可直接 re-export。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from translation_tool.utils.text_processor import (
    load_replace_rules as load_rules_core,
    save_replace_rules as save_rules_core,
)

# --- 檔案路徑設定 ---
# services_impl/config_service.py 位於 app/services_impl/ 底下：
# parents[0]=services_impl, parents[1]=app, parents[2]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = str(PROJECT_ROOT / "config.json")
REPLACE_RULES_PATH = str(PROJECT_ROOT / "replace_rules.json")

def _load_app_config() -> dict[str, Any]:
    """讀取 app 設定（service 層唯一入口）。

    維護目的：
    - 把 service 層對 config_manager 的依賴集中在單一地方。
    - 未來若要改設定來源（例如環境變數/多設定檔/快取），優先改這裡，
      避免 service 各處散落 `load_config()` 呼叫點。
    """

    from translation_tool.utils.config_manager import load_config

    return load_config(CONFIG_PATH)

def _save_app_config(config: dict[str, Any]):
    """儲存 app 設定（service 層唯一入口）。

    與 `_load_app_config()` 成對：
    - service 層只知道「要存設定」，不應綁死底層儲存細節。
    - 之後若要加上寫入驗證/寫入鎖/異動通知，也集中在這裡處理。
    """

    from translation_tool.utils.config_manager import save_config

    return save_config(config, CONFIG_PATH)

# --- 檔案讀寫服務 ---

def load_replace_rules():
    """載入替換規則。

    回傳：
        dict: 規則資料
    """
    return load_rules_core(REPLACE_RULES_PATH)

def save_replace_rules(rules):
    """儲存替換規則。

    參數：
        rules: 規則資料
    """
    save_rules_core(REPLACE_RULES_PATH, rules)

def load_config_json():
    """載入應用程式設定。

    回傳：
        dict: 設定資料
    """
    return _load_app_config()

def save_config_json(config):
    """儲存應用程式設定。

    參數：
        config: 設定資料
    """
    _save_app_config(config)
