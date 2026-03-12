from __future__ import annotations

import math
import re
import threading

import flet as ft


def translate_regex_error(err: re.error) -> str:
    msg = str(err)
    if 'missing )' in msg or 'unterminated subpattern' in msg:
        return '正則表達式缺少結尾括號「)」。'
    if 'bad escape' in msg:
        return '無效的跳脫字元。'
    if 'multiple repeat' in msg:
        return '不合法的重複符號。'
    if 'unterminated character set' in msg:
        return '字元集合（[ ]）未正確結束。'
    if 'unknown extension' in msg:
        return '無效的正則語法。'
    return '正則語法錯誤：' + msg


def validate_rule(view, src: str, dst: str, all_rules, current_index):
    if not src.strip():
        return False, 'from 欄位不可為空'
    try:
        compiled = re.compile(src)
    except re.error as err:
        return False, translate_regex_error(err)
    for idx, rule in enumerate(all_rules):
        if idx != current_index and rule.get('from') == src:
            return False, f'⚠ 與第 {idx + 1} 條規則重複'
    group_refs = re.findall(r'(?:\\+(\d+)|\$(\d+))', dst)
    if group_refs:
        refs = [int(a or b) for a, b in group_refs]
        max_group = compiled.groups
        for ref in refs:
            if ref > max_group:
                return False, f'引用群組 \\{ref} 超出群組數 {max_group}'
    if re.search(r'\\\\(?!\d)', dst):
        return False, '可能存在無效跳脫（\\）'
    return True, ''


def perform_reload(view):
    try:
        rules_data = view._load_rules_core()
        view._run_on_ui_thread(lambda: view._handle_reload_success(rules_data))
    except Exception as err:
        view._run_on_ui_thread(lambda err=err: view._handle_reload_failure(err))


def start_reload_thread(view):
    view.loading_indicator.visible = True
    view.page.update()
    view._show_snack_bar('🔄 正在重新載入規則…', ft.Colors.BLUE_700)
    threading.Thread(target=lambda: perform_reload(view), daemon=True).start()


def start_save_thread(view, clean_rules):
    def worker():
        try:
            view.save_replace_rules(clean_rules)
            view._run_on_ui_thread(lambda: view._show_snack_bar('規則已成功儲存！', view._success_color()))
        except Exception as err:
            msg = f'儲存規則時發生錯誤: {err}'
            view._run_on_ui_thread(lambda msg=msg: view._show_snack_bar(msg, ft.Colors.RED_600))
    threading.Thread(target=worker, daemon=True).start()


def calc_total_pages(total_rules: int, page_size: int) -> int:
    return math.ceil(total_rules / page_size) if total_rules > 0 else 1
