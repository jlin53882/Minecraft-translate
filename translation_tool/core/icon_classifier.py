"""translation_tool/core/icon_classifier.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from .icon_reason import IconRisk


def classify_no_icon_reason(lang_key: str) -> tuple[str, IconRisk]:
    """classify_no_icon_reason 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    k = lang_key.lower()

    if "banner" in k or "pattern" in k:
        return "旗幟 / 樣式為動態合成", IconRisk.IGNORE

    if k.startswith(("jei.", "tooltip.", "itemgroup.", "misc.")):
        return "UI / 分類文字（無 icon）", IconRisk.IGNORE

    if any(c in k for c in ["light", "dark", "red", "blue", "green", "active", "powered"]):
        return "動態染色 / 狀態 icon", IconRisk.WARN

    if k.startswith(("item.", "block.")):
        return "一般物品 / 方塊（可能缺 icon）", IconRisk.DANGER

    return "未知類型（建議確認）", IconRisk.WARN
