"""純函式:把 3 個來源 dict (zh_cn, zh_tw, en_us) 合併成 (final_tw, pending)。

2026-08-02 從 lang_merge_pipeline._process_single_mod 內拆出,
讓 Stage 1 (merge_zhcn_to_zhtw_from_folder) 跟 Stage 2
(merge_extracted_to_assets) 共用同一個 merge 邏輯。

設計:
- 純函式:不直接呼叫 helper 模組,改用 dependency injection
  (apply_replace_rules, recursive_translate_dict, contains_cjk, is_pure_english
   從 caller 傳入,讓 unit test 可以傳 mock)
- 行為 1:1 跟 _process_single_mod 一致 — Stage 1 合併進 helper 後
  行為應該一模一樣(既有 1863+ 個 test 都通過)
- 為什麼拆:原本 Stage 2 自己寫 key-by-key merge 邏輯,
  user 講「要像 Stage 1 處理」,所以 helper 抽出共用。
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


_CJK_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df\U0002a700-\U0002ebef\U00030000-\U0003134f]"
)


@lru_cache(maxsize=4096)
def _contains_cjk_str(s: str) -> bool:
    """Memoized CJK check for string input (module-level)。"""
    return bool(_CJK_RE.search(s))


def contains_cjk(value: Any) -> bool:
    """判斷 value 是否包含 CJK 字元 (str / list / dict)。

    從 _process_single_mod 內 nested _contains_cjk 抽出,
    Stage 1 跟 Stage 2 共用同一實作避免重複維護。
    """
    if isinstance(value, str):
        return _contains_cjk_str(value)
    if isinstance(value, list):
        return any(contains_cjk(x) for x in value)
    if isinstance(value, dict):
        return any(contains_cjk(x) for x in value.values())
    return False


def has_any_text(value: Any) -> bool:
    """結構內是否至少有一段可用的文字 (避免空結構被當 pending)。

    從 _process_single_mod 內 nested has_any_text 抽出。
    """
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, list):
        return any(has_any_text(x) for x in value)
    if isinstance(value, dict):
        return any(has_any_text(x) for x in value.values())
    return False


def is_pure_english(value: Any) -> bool:
    """判斷是否為「不包含 CJK」的內容 (支援結構)。

    從 _process_single_mod 內 nested is_pure_english 抽出。
    規則:
    - 空結構 / 空字串 → False (沒文字)
    - 全 ASCII → True
    - 有 CJK → False
    """
    if not has_any_text(value):
        return False
    return not contains_cjk(value)


def merge_lang_dicts(
    cn_data: Optional[Dict[str, Any]],
    tw_src_data: Optional[Dict[str, Any]],
    en_data: Optional[Dict[str, Any]],
    existing_tw: Optional[Dict[str, Any]],
    rules: List,
    apply_replace_rules: Callable[..., Any],
    recursive_translate_dict: Callable[..., Any],
    contains_cjk: Callable[[Any], bool],
    is_pure_english: Callable[[Any], bool],
    is_from_output_dir: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """合併 3 個 lang 檔來源成 (final_tw, pending) - 跟 Stage 1 邏輯一致。

    規則順序 (從 _process_single_mod Step 4 移植):
        1. 人工 zh_tw 保護:existing_tw 已有 CJK 跳過
        2. zh_tw 來源含 CJK → apply_replace_rules
        3. zh_cn 含 CJK → recursive_translate_dict (S2TW)
        4. 全英文 → pending
        5. fallback → final_tw

    Args:
        cn_data:        zh_cn.json 內容 (或 None/{} 表示無)
        tw_src_data:    zh_tw.json 來源內容 (可能沒有)
        en_data:        en_us.json 內容
        existing_tw:    既有 zh_tw.json (用於人工翻譯保護,可能 None)
        rules:          replace rules list
        apply_replace_rules:  callable(str, rules) -> str
        recursive_translate_dict: callable(value, rules) -> value
        contains_cjk:   callable(value) -> bool
        is_pure_english: callable(value) -> bool
        is_from_output_dir: existing_tw 是否從 output_dir 來
                          (只在 True 時保護人工翻譯)

    Returns:
        (final_tw, pending) tuple
        - final_tw: 最終的 zh_tw dict
        - pending: 待翻譯 (純英文 key)

    Notes:
        行為 1:1 對應 _process_single_mod Step 4 內的 merge 邏輯。
        修改 _process_single_mod 來呼叫這個 helper,Stage 1 行為應該不變。
    """
    cn_data = cn_data or {}
    tw_src_data = tw_src_data or {}
    en_data = en_data or {}
    existing_tw = existing_tw or {}

    # 從既有開始 (人工翻譯不覆寫)
    final_tw: Dict[str, Any] = dict(existing_tw)
    pending: Dict[str, Any] = {}

    # 所有 key 集合
    all_keys = set(cn_data) | set(tw_src_data) | set(en_data)

    for key in all_keys:
        # 1. 人工 zh_tw 保護 (既有 output_dir 來的)
        is_from_output = (
            key in existing_tw and is_from_output_dir
        )
        if is_from_output and contains_cjk(final_tw.get(key, "")):
            continue

        tw_val = tw_src_data.get(key)
        cn_val = cn_data.get(key)
        en_val = en_data.get(key)

        # 2. zh_tw 來源含 CJK → 用規則處理
        if contains_cjk(tw_val):
            if isinstance(tw_val, str):
                final_tw[key] = apply_replace_rules(tw_val, rules)
            else:
                final_tw[key] = recursive_translate_dict(tw_val, rules)
            continue

        # 3. zh_cn 含 CJK → S2TW 翻譯
        if contains_cjk(cn_val):
            final_tw[key] = recursive_translate_dict(cn_val, rules)
            continue

        # 4. 全英文 → pending
        english_source = en_val or cn_val or tw_val
        if english_source is None:
            english_source = ""
        # 空字串不是待翻譯,跳過
        if isinstance(english_source, str) and english_source.strip() == "":
            continue
        if is_pure_english(english_source):
            pending[key] = english_source
            continue

        # 5. fallback - 如果都不是 CJK,設為 english_source
        if english_source is None:
            english_source = ""
        final_tw.setdefault(key, english_source)

    return final_tw, pending
