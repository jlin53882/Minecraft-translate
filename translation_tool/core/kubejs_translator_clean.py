from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import re

_LANG_REF_RE = re.compile(r"^\{.+\}$")


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


def deep_merge_3way_flat_impl(tw: dict, cn: dict, en: dict, *, safe_convert_text_fn: Callable[[str], str]) -> dict:
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
            out[k] = safe_convert_text_fn(v_cn)
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
    """從 KubeJS raw 產出 pending/final 結果。"""
    base = Path(base_dir).resolve()
    out_root = Path(output_dir).resolve() if output_dir else (base / "Output")
    raw_root = Path(raw_dir).resolve() if raw_dir else (out_root / "kubejs" / "raw" / "kubejs")
    pending_root_p = Path(pending_root).resolve() if pending_root else (out_root / "kubejs" / "待翻譯" / "kubejs")
    final_root_p = Path(final_root).resolve() if final_root else (out_root / "kubejs" / "完成" / "kubejs")

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

    copied_other = 0
    for p in other_jsons:
        rel = p.relative_to(raw_root)
        dst = pending_root_p / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
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

        log_debug_fn(f"[KubeJS-CLEAN-DBG] group={group_dir} | en={len(en or {})} cn={len(cn or {})} tw={len(tw or {})}")

        has_twcn = bool(cn or tw)
        rel_group = group_dir.relative_to(raw_root)

        if en:
            if has_twcn:
                available_tw = deep_merge_3way_flat_impl(tw, cn, {}, safe_convert_text_fn=safe_convert_text_fn)
                pending_en = prune_en_by_tw_flat_impl(en, available_tw)
            else:
                pending_en = en

            if pending_en:
                dst_en = pending_root_p / rel_group / "en_us.json"
                write_json_fn(dst_en, pending_en)
                pending_lang_written += 1

        if has_twcn:
            merged_tw = deep_merge_3way_flat_impl(tw, cn, {}, safe_convert_text_fn=safe_convert_text_fn)
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
