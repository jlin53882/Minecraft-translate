"""translation_tool/core/kubejs_translator_clean.py 模組。

用途：KubeJS 翻譯的清理與資料處理功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import json
import re

from translation_tool.plugins.shared.rich_text_shield import (
    shield_text,
    unshield_text,
)

_LANG_REF_RE = re.compile(r"^\{.+\}$")


def _build_reverse_index_impl(final_tw_lookup: dict[str, str]) -> dict[str, str]:
    """建立 reverse_index：{英文文字: 選擇的 canonical key}。

    選擇策略（確定性）：
    1. 優先取「已翻譯的 key」（即 zh_tw 值與英文 key 名不同，表示有真正翻譯）
    2. 若多個已翻譯，取字母序第一個（確定性 tiebreaker）
    3. 若無已翻譯，則取字母序第一個 key

    Returns:
        dict[str, str]: reverse_index，永遠是 str->str（而非 str->list）
    """
    reverse_index: dict[str, str] = {}
    rev_candidates: dict[str, list[tuple[str, bool]]] = {}
    for k, v in final_tw_lookup.items():
        if is_filled_text_impl(v):
            is_translated = bool(
                v.casefold() != k.casefold() if v.isascii() and k.isascii() else v != k
            )
            rev_candidates.setdefault(v, []).append((k, is_translated))

    for en_text, candidates in rev_candidates.items():
        translated = sorted([k for k, t in candidates if t], key=lambda x: x)
        untranslated = sorted([k for k, t in candidates if not t], key=lambda x: x)
        reverse_index[en_text] = (translated or untranslated)[0]

    return reverse_index


def _dedup_pending_en_impl(
    pending_en: dict[str, str], reverse_index: dict[str, str]
) -> dict[str, str]:
    """過濾 pending_en：跳過那些「英文文字已存在於 reverse_index」的 key。

    修復 cross-namespace bug：原本 `k != reverse_index[v]` 比較不同命名空間
    的 key（raw/pending 的 k vs final/zh_tw 的 key），直接比對 key 幾乎
    不會成立。正確邏輯：若同一個翻譯結果 v 已出現在 final
    （即 v in reverse_index），就視為已處理，直接跳過不送 pending。

    Args:
        pending_en: 待翻譯的 en_us 資料（key → 英文文字）
        reverse_index: reverse_index（英文文字 → canonical key）

    Returns:
        dict[str, str]: 過濾後的 pending_en
    """
    return {
        k: v
        for k, v in pending_en.items()
        if not (is_filled_text_impl(v) and v in reverse_index)
    }


def _shielded_convert(text: str, convert_fn: Callable[[str], str]) -> str:
    """對 text 做 shield → convert_fn → unshield 保護。

    用於 OpenCC s2t 轉換時，保護 KubeJS 格式標記（彩色碼、物品ID 等）
    不被轉換破壞。
    """
    # 注意：本函式依賴 translation_tool.plugins.shared.rich_text_shield。
    # 若 rich_text_shield 尚未啟用，此函式退化成直接轉換。
    try:
        from translation_tool.plugins.shared.rich_text_shield import (
            shield_text,
            unshield_text,
        )
    except ImportError:
        return convert_fn(text)

    shielded = shield_text(text)
    if shielded.skip_reason is not None:
        return text
    if not shielded.shields:
        return convert_fn(text)
    converted = convert_fn(shielded.clean)
    return unshield_text(converted, shielded.shields)


def is_filled_text_impl(v: Any) -> bool:
    """判斷是否為有實質內容的文字。"""
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return False
    if _LANG_REF_RE.match(s):
        return False
    return True


def deep_merge_3way_flat_impl(
    tw: dict, cn: dict, en: dict, *, safe_convert_text_fn: Callable[[str], str]
) -> dict:
    """扁平 KubeJS 三語 merge：tw > cn->tw > en。"""
    out = {}
    keys = set(tw.keys()) | set(cn.keys()) | set(en.keys())

    for k in keys:
        v_tw = tw.get(k)
        if is_filled_text_impl(v_tw):
            out[k] = v_tw
            continue

        v_cn = cn.get(k)
        if is_filled_text_impl(v_cn):
            # ✅ Rich Text Shield：保護 zh_cn 值中的 KubeJS 格式後再做 s2t 轉換
            out[k] = _shielded_convert(v_cn, safe_convert_text_fn)
            continue

        v_en = en.get(k)
        if is_filled_text_impl(v_en):
            out[k] = v_en

    return out


def prune_en_by_tw_flat_impl(en_map: dict, tw_available: dict) -> dict:
    """剪掉 tw 已有內容的 en key。"""
    out = {}
    for k, v in en_map.items():
        if is_filled_text_impl(tw_available.get(k)):
            continue
        out[k] = v
    return out


def clean_kubejs_from_raw_impl(
    base_dir: str,
    *,
    output_dir: str | None = None,
    raw_dir: str | None = None,
    pending_root: str | None = None,
    final_root: str | None = None,
    read_json_dict_fn: Callable[[Path], dict],
    write_json_fn: Callable[[Path, dict], None],
    safe_convert_text_fn: Callable[[str], str],
    log_debug_fn: Callable[..., None],
    log_info_fn: Callable[..., None],
) -> dict:
    """實作：將 KubeJS 原始 lang 檔（en_us/zh_cn/zh_tw）做三方合併，產出待翻譯 en_us 與完成品 zh_tw。

    Args:
        base_dir: Modpack 根目錄。
        output_dir: 輸出根目錄（預設 base_dir/Output）。
        raw_dir: 原始 lang 檔所在目錄。
        pending_root: 待翻譯 en_us 的輸出目錄。
        final_root: 合併後 zh_tw 的輸出目錄。
        read_json_dict_fn: 讀取 JSON 檔的函式。
        write_json_fn: 寫入 JSON 檔的函式。
        safe_convert_text_fn: 簡體轉繁體的函式。
        log_debug_fn: Debug 層級日誌函式。
        log_info_fn: Info 層級日誌函式。
    Returns:
        dict: 處理摘要（群組數、寫入檔案數等）。
    """
    base = Path(base_dir).resolve()
    out_root = Path(output_dir).resolve() if output_dir else (base / "Output")
    raw_root = (
        Path(raw_dir).resolve() if raw_dir else (out_root / "kubejs" / "raw" / "kubejs")
    )
    pending_root_p = (
        Path(pending_root).resolve()
        if pending_root
        else (out_root / "kubejs" / "待翻譯" / "kubejs")
    )
    final_root_p = (
        Path(final_root).resolve()
        if final_root
        else (out_root / "kubejs" / "完成" / "kubejs")
    )

    pending_root_p.mkdir(parents=True, exist_ok=True)
    final_root_p.mkdir(parents=True, exist_ok=True)

    lang_files = []
    other_jsons = []
    for p in raw_root.rglob("*.json"):
        pp = str(p).replace("\\", "/")
        if "/lang/" in pp:
            lang_files.append(p)
        else:
            other_jsons.append(p)

    # 建立 zh_tw lookup table：用於過濾 client_scripts/*.json
    # 已翻譯的 key（有 zh_tw 對應）→ skip；未翻譯 → 保留到 pending
    tw_lookup: dict[str, str] = {}
    if final_root_p.exists():
        for tw_file in final_root_p.rglob("zh_tw.json"):
            tw_data = read_json_dict_fn(tw_file)
            if tw_data:
                tw_lookup.update(tw_data)
    # 同時從 raw_root 的 lang/zh_tw.json 讀取（確保新翻譯也被納入）
    for tw_file in raw_root.rglob("zh_tw.json"):
        tw_data = read_json_dict_fn(tw_file)
        if tw_data:
            tw_lookup.update(
                deep_merge_3way_flat_impl(
                    tw_data, {}, {}, safe_convert_text_fn=safe_convert_text_fn
                )
            )

    copied_other = 0
    for p in other_jsons:
        rel = p.relative_to(raw_root)
        dst = pending_root_p / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        if "client_scripts" in str(p):
            # 對 client_scripts/*.json 做三語合併比對過濾
            # client_scripts JSON key 格式：tooltips.js|modid:item.tooltip.0
            # zh_tw.json key 格式：modid:item（無 .tooltip.N 後綴）
            # → 需剝除前綴與 .tooltip.N 後綴才能正確比對
            data = read_json_dict_fn(p)
            if data:
                filtered = {}
                for k, v in data.items():
                    # 解析 key：去掉前綴 tooltips.js| 和 .tooltip.N 後綴
                    lookup_key = k.split("|", 1)[-1] if "|" in k else k
                    lookup_key = re.sub(r"\.tooltip\.\d+$", "", lookup_key)
                    lookup_key = re.sub(r"\[.*?\]", "", lookup_key).strip()
                    # 有 zh_tw 翻譯 → skip（視為 cache hit）；無 → 保留
                    if lookup_key and lookup_key not in tw_lookup:
                        # ✅ 對簡體中文值做 OpenCC 轉換（s2tw），轉為繁體中文
                        # ✅ Rich Text Shield：保護 KubeJS 格式後再做 s2t 轉換
                        v_converted = _shielded_convert(v, safe_convert_text_fn)
                        filtered[k] = v_converted
                if filtered:
                    dst.write_text(
                        json.dumps(filtered, indent=2, ensure_ascii=False), "utf-8"
                    )
                    copied_other += 1
                # else: 全部被過濾，不寫入也不計入 copied_other
            else:
                dst.write_bytes(p.read_bytes())
                copied_other += 1
        else:
            dst.write_bytes(p.read_bytes())
            copied_other += 1

    groups: dict[Path, dict[str, Path]] = {}
    for p in lang_files:
        group_dir = p.parent
        lang_name = p.stem.lower()
        groups.setdefault(group_dir, {})[lang_name] = p

    merged_lang_written = 0
    pending_lang_written = 0

    for group_dir, files_map in groups.items():
        en = read_json_dict_fn(files_map.get("en_us"))
        cn = read_json_dict_fn(files_map.get("zh_cn"))
        tw = read_json_dict_fn(files_map.get("zh_tw"))

        log_debug_fn(
            f"[KubeJS-CLEAN-DBG] group={group_dir} | en={len(en or {})} cn={len(cn or {})} tw={len(tw or {})}"
        )

        has_twcn = bool(cn or tw)
        rel_group = group_dir.relative_to(raw_root)

        if en:
            if has_twcn:
                available_tw = deep_merge_3way_flat_impl(
                    tw, cn, {}, safe_convert_text_fn=safe_convert_text_fn
                )
                pending_en = prune_en_by_tw_flat_impl(en, available_tw)
            else:
                pending_en = en

            # ── 雙軌去重（reverse_index dedup）───────────────────────────────
            # 目的：若某英文文字（value）已出現在 final/zh_tw.json（不同 key），
            #       表示該英文原文已有翻譯，不需要再送 pending。
            # 建立 reverse_index：{英文文字: [key1, key2, ...]}
            if pending_en and final_root_p.exists():
                # 從 final/zh_tw.json 建立 final_tw_lookup（key → 翻譯值）
                final_tw_lookup: dict[str, str] = {}
                for tw_file in final_root_p.rglob("zh_tw.json"):
                    tw_data = read_json_dict_fn(tw_file)
                    if tw_data:
                        final_tw_lookup.update(tw_data)

                if final_tw_lookup:
                    # 使用確定性 reverse_index 建構 + cross-namespace dedup
                    reverse_index = _build_reverse_index_impl(final_tw_lookup)
                    pending_en = _dedup_pending_en_impl(pending_en, reverse_index)
            # ── 雙軌去重 end ───────────────────────────────────────────────

            if pending_en:
                dst_en = pending_root_p / rel_group / "en_us.json"
                write_json_fn(dst_en, pending_en)
                pending_lang_written += 1

        if has_twcn:
            merged_tw = deep_merge_3way_flat_impl(
                tw, cn, {}, safe_convert_text_fn=safe_convert_text_fn
            )
            dst_tw = final_root_p / rel_group / "zh_tw.json"
            write_json_fn(dst_tw, merged_tw)
            merged_lang_written += 1

    log_info_fn(
        f"[KubeJS-CLEAN] 處理完畢！群組數: {len(groups)} | 產出待翻譯: {pending_lang_written} | 產出完成品: {merged_lang_written} | 複製其他檔案: {copied_other}"
    )

    return {
        "raw_root": str(raw_root),
        "pending_root": str(pending_root_p),
        "final_root": str(final_root_p),
        "groups": len(groups),
        "pending_lang_written": pending_lang_written,
        "merged_lang_written": merged_lang_written,
        "copied_other_jsons": copied_other,
    }
