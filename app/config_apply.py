"""Central apply-timing metadata shared by config UI and maintenance docs."""

from __future__ import annotations

from typing import Final, TypedDict


class ApplyRule(TypedDict):
    timing: str
    note: str


CONFIG_APPLY_RULES: Final[dict[str, ApplyRule]] = {
    "translator.cache_directory": {
        "timing": "next_reload",
        "note": "持久化後不切換現行工作；下次啟動或明確重載才套用。",
    },
    "lm_translator.keys": {
        "timing": "next_request",
        "note": "下次 API 請求讀取；不改變已送出的請求。",
    },
    "lm_translator.models": {
        "timing": "next_batch",
        "note": "下一批次讀取；進行中的批次維持原設定。",
    },
    "lm_translator.max_output_tokens": {
        "timing": "next_batch",
        "note": "下一批次讀取；per-model override 優先於全域值；0 表示不送 maxOutputTokens。",
    },
    "lm_translator.max_output_token_budget": {
        "timing": "next_batch",
        "note": "下一批次讀取；用於切批估算。",
    },
    "lm_translator.max_input_token_budget": {
        "timing": "next_batch",
        "note": "下一批次讀取；用於切批估算。",
    },
    "lm_translator.key_failure_cooldown_sec": {
        "timing": "next_request",
        "note": "下一次 API key 判斷讀取；不改變已送出的請求。",
    },
    "lm_translator.models.*.max_output_tokens": {
        "timing": "next_batch",
        "note": "下一批次讀取；0 表示不送 maxOutputTokens。",
    },
    "lm_translator.lm_translate_folder_name": {
        "timing": "next_task",
        "note": "使用時才讀取；存檔後下次執行機器翻譯即套用，不需重啟。",
    },
    "output_bundler.output_zip_name": {
        "timing": "next_task",
        "note": "打包時才讀取；輸入框提示文字於重新開啟頁面後更新。",
    },
    "logging.log_dir": {
        "timing": "next_task",
        "note": "應用日誌於啟動時建立；錯誤記錄（errors_*.log）每次寫入時讀取。",
    },
    "species_cache.*": {
        "timing": "restart",
        "note": "目前模組初始化後不熱切換。",
    },
}


def get_apply_rule(path: str) -> ApplyRule | None:
    """Return exact metadata, then wildcard model metadata when applicable."""
    if path in CONFIG_APPLY_RULES:
        return CONFIG_APPLY_RULES[path]
    if path.startswith("lm_translator.models.") and path.endswith(".max_output_tokens"):
        return CONFIG_APPLY_RULES["lm_translator.models.*.max_output_tokens"]
    if path.startswith("species_cache."):
        return CONFIG_APPLY_RULES["species_cache.*"]
    return None


def apply_timing_note(path: str, fallback: str = "") -> str:
    """Return the user-facing apply-timing note from the central contract."""
    rule = get_apply_rule(path)
    return rule["note"] if rule is not None else fallback
