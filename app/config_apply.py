"""設定存檔後「何時生效」的中央契約：設定頁說明、存檔提示、自動重載都以這份表為準。

timing 的意義：

- ``immediate``     存檔後立即套用到執行中的程式。
- ``next_request``  進行中的任務會在下一次 API 請求讀取到新值。
- ``next_batch``    進行中的任務會在下一個批次讀取到新值。
- ``next_task``     進行中的任務不受影響，下次開始任務才套用（沒登記的設定都是這種）。
- ``when_idle``     沒有任務時存檔立即套用；有任務進行中則等任務結束後才套用。
- ``restart``       需要重新啟動才套用。
"""

from __future__ import annotations

from typing import Final, TypedDict

# 沒有登記在 CONFIG_APPLY_RULES 的設定：下次任務才讀取（絕大多數引擎設定都是如此）。
DEFAULT_TIMING: Final = "next_task"
DEFAULT_NOTE: Final = "下次執行任務時套用。"

ALL_TIMINGS: Final = frozenset(
    {
        "immediate",
        "next_request",
        "next_batch",
        "next_task",
        "when_idle",
        "restart",
    }
)


class ApplyRule(TypedDict):
    timing: str
    note: str


_IMMEDIATE_LOG = "存檔後立即套用到執行中的日誌，不必重啟。"

CONFIG_APPLY_RULES: Final[dict[str, ApplyRule]] = {
    "ui.theme_mode": {"timing": "immediate", "note": "切換後立即套用。"},
    "logging.log_level": {"timing": "immediate", "note": _IMMEDIATE_LOG},
    "logging.log_format": {"timing": "immediate", "note": _IMMEDIATE_LOG},
    "logging.log_dir": {
        "timing": "restart",
        "note": "應用日誌需重啟才換資料夾；錯誤記錄（errors_*.log）下次寫入時即套用。",
    },
    "output_bundler.output_zip_name": {
        "timing": "next_task",
        "note": "打包時才讀取；打包頁的輸入框提示會在存檔後立即更新。",
    },
    "ui_logging.tail_lines": {
        "timing": "next_task",
        "note": "下次開始機器翻譯時套用，不必重開頁面。",
    },
    "translator.cache_directory": {
        "timing": "when_idle",
        "note": "存檔後自動重載快取與搜尋索引；有任務進行中時，等任務結束後才切換，避免新舊資料混用。",
    },
    "lm_translator.keys": {
        "timing": "next_request",
        "note": "下次 API 請求讀取；不改變已送出的請求。",
    },
    "lm_translator.models": {
        "timing": "next_batch",
        "note": "下一批次讀取；進行中的批次維持原設定。",
    },
    "lm_translator.temperature": {
        "timing": "next_batch",
        "note": "下一批次讀取。",
    },
    "lm_translator.patchouli_system_prompt": {
        "timing": "next_batch",
        "note": "下一批次讀取。",
    },
    "lm_translator.lang_system_prompt": {
        "timing": "next_batch",
        "note": "下一批次讀取。",
    },
    "lm_translator.rate_limit.*": {
        "timing": "next_request",
        "note": "下次 API 請求讀取。",
    },
    "lm_translator.rpm_cooldown_sec": {
        "timing": "next_request",
        "note": "下次 API 請求讀取。",
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
    "lm_translator.token_budget_enabled": {
        "timing": "next_batch",
        "note": "下一批次讀取。",
    },
    "lm_translator.output_token_factor": {
        "timing": "next_batch",
        "note": "下一批次讀取。",
    },
    "lm_translator.budget_min_scale": {
        "timing": "next_batch",
        "note": "下一批次讀取。",
    },
    "lm_translator.budget_recover_after": {
        "timing": "next_batch",
        "note": "下一批次讀取。",
    },
    "lm_translator.budget_recover_factor": {
        "timing": "next_batch",
        "note": "下一批次讀取。",
    },
    "lm_translator.key_failure_cooldown_sec": {
        "timing": "next_request",
        "note": "下一次 API key 判斷讀取；不改變已送出的請求。",
    },
    "lm_translator.models.*.max_output_tokens": {
        "timing": "next_batch",
        "note": "下一批次讀取；0 表示不送 maxOutputTokens。",
    },
    "species_cache.*": {
        "timing": "restart",
        "note": "目前模組初始化後不熱切換，需重啟才套用。",
    },
}


def get_apply_rule(path: str) -> ApplyRule | None:
    """Return exact metadata, then wildcard metadata when applicable."""
    if path in CONFIG_APPLY_RULES:
        return CONFIG_APPLY_RULES[path]
    if path.startswith("lm_translator.models.") and path.endswith(".max_output_tokens"):
        return CONFIG_APPLY_RULES["lm_translator.models.*.max_output_tokens"]
    if path.startswith("lm_translator.rate_limit."):
        return CONFIG_APPLY_RULES["lm_translator.rate_limit.*"]
    if path.startswith("species_cache."):
        return CONFIG_APPLY_RULES["species_cache.*"]
    return None


def timing_of(path: str) -> str:
    """設定的套用時機（沒登記的設定為 ``DEFAULT_TIMING``）。"""
    rule = get_apply_rule(path)
    return rule["timing"] if rule is not None else DEFAULT_TIMING


def apply_timing_note(path: str, fallback: str = "") -> str:
    """Return the user-facing apply-timing note from the central contract."""
    rule = get_apply_rule(path)
    return rule["note"] if rule is not None else fallback


def timing_note(path: str) -> str:
    """設定頁顯示用：已登記的說明，否則為預設的「下次執行任務時套用」。"""
    return apply_timing_note(path, DEFAULT_NOTE)


# 存檔提示的訊息
NOTICE_NEXT_TASK = "進行中的任務不受影響，下次任務才套用"
NOTICE_NEXT_BATCH = "進行中的任務會在下一批次 / 下次請求套用新值"
NOTICE_WHEN_IDLE = "快取資料夾已變更：任務結束後會自動重載"
NOTICE_RESTART = "部分設定需重新啟動才會套用"


def save_notice(changed_paths, *, tasks_running: bool) -> str | None:
    """依中央表決定「設定已儲存」之後要提醒使用者的訊息；不需要提醒時回傳 None。

    - ``immediate`` 的設定不提醒（已經套用了）。
    - ``restart`` 一律提醒（不論有沒有任務）。
    - 其餘只有在「有任務進行中」才提醒，且依時機給出不同說法。
    """
    timings = {timing_of(p) for p in changed_paths}
    parts: list[str] = []
    if tasks_running:
        if "next_task" in timings:
            parts.append(NOTICE_NEXT_TASK)
        if timings & {"next_batch", "next_request"}:
            parts.append(NOTICE_NEXT_BATCH)
        if "when_idle" in timings:
            parts.append(NOTICE_WHEN_IDLE)
    if "restart" in timings:
        parts.append(NOTICE_RESTART)
    if not parts:
        return None
    return "設定已儲存；" + "；".join(parts) + "。"
