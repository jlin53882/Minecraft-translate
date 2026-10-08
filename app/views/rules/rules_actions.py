from __future__ import annotations

import math
import re

import flet as ft  # noqa: F401

from app.tasks.operation_registry import (
    CancellationPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui.design import C
from app.ui.snack import show_snack
from translation_tool.utils.log_unit import log_error


def translate_regex_error(err: re.error) -> str:
    """将 Python 正则表达式错误转换为中文用户提示"""
    msg = str(err)
    if "missing )" in msg or "unterminated subpattern" in msg:
        return "正則表達式缺少結尾括號「)」。"
    if "bad escape" in msg:
        return "無效的跳脫字元。"
    if "multiple repeat" in msg:
        return "不合法的重複符號。"
    if "unterminated character set" in msg:
        return "字元集合（[ ]）未正確結束。"
    if "unknown extension" in msg:
        return "無效的正則語法。"
    return "正則語法錯誤：" + msg


def validate_rule(view, src: str, dst: str, all_rules, current_index):
    """验证替换规则的语法正确性和逻辑一致性"""
    if not src.strip():
        return False, "from 欄位不可為空"
    try:
        compiled = re.compile(src)
    except re.error as err:
        return False, translate_regex_error(err)
    for idx, rule in enumerate(all_rules):
        if idx != current_index and rule.get("from") == src:
            return False, f"⚠ 與第 {idx + 1} 條規則重複"
    group_refs = re.findall(r"(?:\\+(\d+)|\$(\d+))", dst)
    if group_refs:
        refs = [int(a or b) for a, b in group_refs]
        max_group = compiled.groups
        for ref in refs:
            if ref > max_group:
                return False, f"引用群組 \\{ref} 超出群組數 {max_group}"
    if re.search(r"\\\\(?!\d)", dst):
        return False, "可能存在無效跳脫（\\）"
    return True, ""


def perform_reload(view):
    """执行规则重新加载，读取配置文件并更新界面"""
    try:
        rules_data = view._load_rules_core()
        view._run_on_ui_thread(lambda: view._handle_reload_success(rules_data))
    except Exception as err:  # noqa: BLE001
        log_error(f"[Rules] 重新載入規則失敗：{err!r}", exc_info=True)
        view._run_on_ui_thread(lambda err=err: view._handle_reload_failure(err))


def start_reload_thread(view):
    """在后台线程启动规则重新加载流程"""
    view.loading_indicator.visible = True
    view.page.update()
    show_snack(view.page, "🔄 正在重新載入規則…", C.DIA)
    launched = launch_page_operation(
        view.page,
        lambda: perform_reload(view),
        name="規則重新載入",
        owner="rules",
        cancellation=CancellationPolicy.NON_CANCELLABLE,
        shutdown=ShutdownPolicy.DRAIN_ONLY,
    )
    if not launched:
        view.loading_indicator.visible = False
        show_snack(view.page, "應用程式正在關閉，無法啟動新工作", C.GOLD)
        view.page.update()


def calc_total_pages(total_rules: int, page_size: int) -> int:
    """计算规则列表的总页数"""
    return math.ceil(total_rules / page_size) if total_rules > 0 else 1
