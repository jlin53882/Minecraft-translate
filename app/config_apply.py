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

from translation_tool.utils.config_schema import SETTINGS

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


# 套用時機的唯一來源是設定 schema（``translation_tool/utils/config_schema.py`` 每個 Setting 的
# ``timing`` / ``timing_note``）；這張表由它建出。下面只補 schema 以外的路徑與樣式規則。
CONFIG_APPLY_RULES: Final[dict[str, ApplyRule]] = {
    setting.path: {"timing": setting.timing, "note": setting.timing_note}
    for setting in SETTINGS
    if setting.timing != DEFAULT_TIMING or setting.timing_note
}

# schema 沒有對應葉節點的設定
CONFIG_APPLY_RULES["ui_logging.tail_lines"] = {
    "timing": "next_task",
    "note": "下次開始機器翻譯時套用，不必重開頁面。",
}
# 樣式規則：每個模型自己的輸出上限（lm_translator.models 內的動態 key）
CONFIG_APPLY_RULES["lm_translator.models.*.max_output_tokens"] = {
    "timing": "next_batch",
    "note": "留空沿用全域輸出上限；0 不指定輸出上限；其他數值於下一批套用。",
}


def get_apply_rule(path: str) -> ApplyRule | None:
    """Return exact metadata, then wildcard metadata when applicable."""
    if path in CONFIG_APPLY_RULES:
        return CONFIG_APPLY_RULES[path]
    if path.startswith("lm_translator.models.") and path.endswith(".max_output_tokens"):
        return CONFIG_APPLY_RULES["lm_translator.models.*.max_output_tokens"]
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
