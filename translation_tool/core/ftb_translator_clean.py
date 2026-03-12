from __future__ import annotations

import os
import re
from typing import Any, Callable

_LANG_REF_RE = re.compile(r"^\{ftbquests\..+\}$")


def _is_filled_text(v) -> bool:
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return False
    if _LANG_REF_RE.match(s):
        return False
    return True


def deep_merge_3way(zh_tw: dict, zh_cn: dict, en_us: dict) -> dict:
    """優先順序：zh_tw > (zh_cn 轉繁) > en_us。"""

    def is_empty(v):
        return v is None or v == "" or v == {} or v == []

    def merge(a, b, c):
        if isinstance(a, dict) or isinstance(b, dict) or isinstance(c, dict):
            a = a if isinstance(a, dict) else {}
            b = b if isinstance(b, dict) else {}
            c = c if isinstance(c, dict) else {}
            out = {}
            for k in set(a.keys()) | set(b.keys()) | set(c.keys()):
                out[k] = merge(a.get(k), b.get(k), c.get(k))
            return out

        if not is_empty(a):
            return a
        if not is_empty(b):
            return safe_convert_text(b) if isinstance(b, str) else b
        return c

    from ..utils.text_processor import safe_convert_text

    return merge(zh_tw, zh_cn, en_us)


def prune_en_us_by_zh_tw(en_us: Any, zh_tw: Any) -> Any:
    """從 en_us 中刪掉 zh_tw 已有內容的部分。"""

    def is_filled(v: Any) -> bool:
        return v is not None and v != "" and v != {} and v != []

    if isinstance(en_us, dict) and isinstance(zh_tw, dict):
        out = {}
        for k, v in en_us.items():
            zh_v = zh_tw.get(k)
            if is_filled(zh_v):
                continue
            pruned = prune_en_us_by_zh_tw(v, zh_v)
            if is_filled(pruned):
                out[k] = pruned
        return out

    if isinstance(en_us, list):
        return en_us
    return en_us


def prune_flat_en_by_tw(en_map: dict, tw_available: dict) -> dict:
    """針對扁平 dict，只保留 tw 尚未覆蓋的 en key。"""
    out = {}
    for k, v in en_map.items():
        tw_v = tw_available.get(k)
        if _is_filled_text(tw_v):
            continue
        out[k] = v
    return out


def clean_ftbquests_from_raw_impl(
    base_dir: str,
    *,
    output_dir: str | None = None,
    orjson_loads,
    orjson_dump_file_fn: Callable[[object, object], None],
    log_info_fn: Callable[..., None],
) -> dict:
    """從 raw 資料產出 pending/en_us 與整理後 zh_tw。"""
    out_root = output_dir or os.path.join(base_dir, "Output")
    raw_root = os.path.join(
        out_root, "ftbquests", "raw", "config", "ftbquests", "quests", "lang"
    )

    def load_json(lang: str, name: str):
        path = os.path.join(raw_root, lang, name)
        if not os.path.isfile(path):
            return {}
        with open(path, "rb") as f:
            return orjson_loads(f.read())

    en_lang = load_json("en_us", "ftb_lang.json")
    en_quests = load_json("en_us", "ftb_quests.json")
    cn_lang = load_json("zh_cn", "ftb_lang.json")
    cn_quests = load_json("zh_cn", "ftb_quests.json")
    tw_lang = load_json("zh_tw", "ftb_lang.json")
    tw_quests = load_json("zh_tw", "ftb_quests.json")

    has_twcn_source = bool(tw_lang or tw_quests or cn_lang or cn_quests)

    if has_twcn_source:
        available_lang_tw = deep_merge_3way(tw_lang, cn_lang, {})
        available_quests_tw = deep_merge_3way(tw_quests, cn_quests, {})
        pending_lang = prune_flat_en_by_tw(en_lang, available_lang_tw)
        pending_quests = prune_flat_en_by_tw(en_quests, available_quests_tw)
    else:
        pending_lang = en_lang
        pending_quests = en_quests

    pending_en_root = os.path.join(
        out_root,
        "ftbquests",
        "待翻譯",
        "config",
        "ftbquests",
        "quests",
        "lang",
        "en_us",
    )
    os.makedirs(pending_en_root, exist_ok=True)
    with open(os.path.join(pending_en_root, "ftb_lang.json"), "wb") as f:
        orjson_dump_file_fn(pending_lang, f)
    with open(os.path.join(pending_en_root, "ftb_quests.json"), "wb") as f:
        orjson_dump_file_fn(pending_quests, f)

    zh_tw_root = os.path.join(
        out_root,
        "ftbquests",
        "整理後",
        "config",
        "ftbquests",
        "quests",
        "lang",
        "zh_tw",
    )

    if has_twcn_source:
        final_lang_tw = deep_merge_3way(tw_lang, cn_lang, {})
        final_quests_tw = deep_merge_3way(tw_quests, cn_quests, {})
        os.makedirs(zh_tw_root, exist_ok=True)
        with open(os.path.join(zh_tw_root, "ftb_lang.json"), "wb") as f:
            orjson_dump_file_fn(final_lang_tw, f)
        with open(os.path.join(zh_tw_root, "ftb_quests.json"), "wb") as f:
            orjson_dump_file_fn(final_quests_tw, f)
        return {
            "raw_root": raw_root,
            "out_root": out_root,
            "en_pending_dir": pending_en_root,
            "zh_tw_dir": zh_tw_root,
            "has_twcn_source": True,
        }

    log_info_fn("FTB Quests 只有 en_us：已輸出待翻譯，跳過 zh_tw 產出")
    return {
        "raw_root": raw_root,
        "out_root": out_root,
        "en_pending_dir": pending_en_root,
        "zh_tw_dir": None,
        "has_twcn_source": False,
    }
