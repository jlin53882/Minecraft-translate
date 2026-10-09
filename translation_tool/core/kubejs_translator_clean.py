"""translation_tool/core/kubejs_translator_clean.py 模組。

用途：KubeJS 翻譯的清理與資料處理功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from translation_tool.core.kubejs_translator_state import (
    atomic_write_json,
    load_owned_files,
    sync_managed_tree,
)
from translation_tool.plugins.shared.rich_text_shield import (
    shield_text,
    unshield_text,
)

_LANG_REF_RE = re.compile(r"^\{.+\}$")


def _shielded_convert(text: str, convert_fn: Callable[[str], str]) -> str:
    """對 text 做 shield → convert_fn → unshield 保護。

    用於 OpenCC s2t 轉換時，保護 KubeJS 格式標記（彩色碼、物品ID 等）
    不被轉換破壞。
    """
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
    return not _LANG_REF_RE.match(s)


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
    """剪掉同一 identity 已有且與英文來源不同的繁中內容。"""
    out = {}
    for k, v in en_map.items():
        translated = tw_available.get(k)
        same_as_source = (
            isinstance(v, str)
            and isinstance(translated, str)
            and (
                v.casefold() == translated.casefold()
                if v.isascii() and translated.isascii()
                else v == translated
            )
        )
        if is_filled_text_impl(translated) and not same_as_source:
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
    previous_final_root: str | None = None,
    manual_final_root: str | None = None,
    previous_final_owned: dict[str, str] | None = None,
    current_source_provenance: dict[str, Any] | None = None,
    previous_source_provenance: dict[str, Any] | None = None,
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

    lang_files: list[Path] = []
    other_jsons: list[Path] = []
    for path in raw_root.rglob("*.json"):
        relative_parts = {part.lower() for part in path.relative_to(raw_root).parts}
        if "lang" in relative_parts:
            lang_files.append(path)
        else:
            other_jsons.append(path)

    pending_outputs: dict[str, dict[str, Any] | bytes] = {}
    copied_other = 0
    for path in other_jsons:
        relative = path.relative_to(raw_root)
        data = read_json_dict_fn(path)
        if not data:
            continue
        if "client_scripts" in {part.lower() for part in path.parts}:
            # Lang IDs cannot prove that one particular script tooltip is translated.
            # Keep the original JS identity and let the translator/injector use it.
            filtered = {
                key: _shielded_convert(value, safe_convert_text_fn)
                if isinstance(value, str)
                else value
                for key, value in data.items()
            }
            if filtered:
                pending_outputs[relative.as_posix()] = filtered
                copied_other += 1
        else:
            pending_outputs[relative.as_posix()] = path.read_bytes()
            copied_other += 1

    groups: dict[Path, dict[str, Path]] = {}
    for path in lang_files:
        groups.setdefault(path.parent, {})[path.stem.lower()] = path

    previous_root = (
        Path(previous_final_root).resolve() if previous_final_root else final_root_p
    )
    manual_root = Path(manual_final_root).resolve() if manual_final_root else None
    previous_final_owned = previous_final_owned or {}
    current_source_provenance = current_source_provenance or {}
    previous_source_provenance = previous_source_provenance or {}
    pending_lang_written = 0
    merged_lang_written = 0
    previous_final_keys_preserved = 0
    final_outputs: dict[str, dict[str, Any]] = {}
    provenance_conflicts: list[dict[str, str]] = []

    def key_evidence(
        provenance: dict[str, Any], relative_group: Path, key: str
    ) -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        for language in ("en_us", "zh_cn", "zh_tw"):
            relative = (relative_group / f"{language}.json").as_posix()
            file_data = provenance.get(relative, {})
            key_data = file_data.get(key, {}) if isinstance(file_data, dict) else {}
            if isinstance(key_data, dict) and language in key_data:
                evidence[language] = key_data[language]
        return evidence

    def language_source_changed(
        current_evidence: dict[str, Any],
        previous_evidence: dict[str, Any],
        language: str,
    ) -> bool:
        return current_evidence.get(language) != previous_evidence.get(language)

    def read_final(path: Path) -> dict[str, Any]:
        return read_json_dict_fn(path)

    for group_dir, files_map in groups.items():
        en = read_json_dict_fn(files_map.get("en_us"))
        cn = read_json_dict_fn(files_map.get("zh_cn"))
        tw = read_json_dict_fn(files_map.get("zh_tw"))
        relative_group = group_dir.relative_to(raw_root)
        previous_output = read_final(previous_root / relative_group / "zh_tw.json")
        current_keys = set(en) | set(cn) | set(tw)
        old_final: dict[str, Any] = {}
        manual_final: dict[str, Any] = {}
        relative_final = (relative_group / "zh_tw.json").as_posix()
        manual_path = manual_root / Path(relative_final) if manual_root else None
        recorded_hash = previous_final_owned.get(relative_final)
        manual_file_edited = False
        if manual_path is not None and recorded_hash and manual_path.is_file():
            try:
                manual_file_edited = (
                    sha256(manual_path.read_bytes()).hexdigest() != recorded_hash
                )
            except OSError:
                provenance_conflicts.append(
                    {
                        "path": relative_final,
                        "key": "*",
                        "kind": "manual_final_unreadable",
                        "decision": "保留公開檔並回到待翻譯快照",
                    }
                )
        manual_candidate = (
            read_final(manual_path)
            if manual_path is not None and manual_file_edited
            else {}
        )
        current_keys.update(previous_output)
        current_keys.update(manual_candidate)

        for key in current_keys:
            current_evidence = key_evidence(
                current_source_provenance, relative_group, str(key)
            )
            previous_evidence = key_evidence(
                previous_source_provenance, relative_group, str(key)
            )
            if (
                key in previous_output
                and current_evidence
                and current_evidence == previous_evidence
            ):
                old_final[key] = previous_output[key]
            elif key in previous_output and current_evidence != previous_evidence:
                provenance_conflicts.append(
                    {
                        "path": relative_final,
                        "key": str(key),
                        "kind": "stale_previous_translation",
                        "decision": "不沿用舊譯文；使用本輪來源或保留待翻譯",
                    }
                )

            if key in manual_candidate:
                if current_evidence and current_evidence == previous_evidence:
                    manual_final[key] = manual_candidate[key]
                else:
                    provenance_conflicts.append(
                        {
                            "path": relative_final,
                            "key": str(key),
                            "kind": "manual_final_source_changed",
                            "decision": "保留公開手動檔；此 key 不併入新快照並回到待翻譯",
                        }
                    )
        previous_final_keys_preserved += len(old_final)

        log_debug_fn(
            f"[KubeJS-CLEAN-DBG] group={group_dir} | en={len(en)} cn={len(cn)} tw={len(tw)} old_tw={len(old_final)}"
        )

        # Prior outputs are usable only when the source evidence still matches.
        # A verified user edit outranks current sources; changed zh_tw/zh_cn
        # source data outranks old AI output; unchanged AI output may be reused.
        merged_tw: dict[str, Any] = {}
        for key in current_keys:
            current_evidence = key_evidence(
                current_source_provenance, relative_group, str(key)
            )
            previous_evidence = key_evidence(
                previous_source_provenance, relative_group, str(key)
            )
            english_changed = language_source_changed(
                current_evidence, previous_evidence, "en_us"
            )
            tw_source_changed = language_source_changed(
                current_evidence, previous_evidence, "zh_tw"
            )
            cn_source_changed = language_source_changed(
                current_evidence, previous_evidence, "zh_cn"
            )
            if key in manual_final:
                merged_tw[key] = manual_final[key]
            elif is_filled_text_impl(tw.get(key)) and (
                tw_source_changed or not previous_evidence
            ):
                merged_tw[key] = tw[key]
            elif is_filled_text_impl(cn.get(key)) and (
                cn_source_changed or not previous_evidence
            ):
                merged_tw[key] = _shielded_convert(cn[key], safe_convert_text_fn)
            elif key in old_final and current_evidence == previous_evidence:
                merged_tw[key] = old_final[key]
            elif is_filled_text_impl(tw.get(key)) and not english_changed:
                merged_tw[key] = tw[key]
            elif is_filled_text_impl(cn.get(key)) and not english_changed:
                merged_tw[key] = _shielded_convert(cn[key], safe_convert_text_fn)

        pending_en = prune_en_by_tw_flat_impl(en, merged_tw)
        if pending_en:
            pending_outputs[(relative_group / "en_us.json").as_posix()] = pending_en
            pending_lang_written += 1
        if merged_tw:
            final_outputs[(relative_group / "zh_tw.json").as_posix()] = merged_tw
            merged_lang_written += 1

    pending_root_p.parent.mkdir(parents=True, exist_ok=True)
    final_root_p.parent.mkdir(parents=True, exist_ok=True)
    pending_stage = Path(
        tempfile.mkdtemp(
            prefix=f".{pending_root_p.name}.clean-", dir=pending_root_p.parent
        )
    )
    final_stage = Path(
        tempfile.mkdtemp(prefix=f".{final_root_p.name}.clean-", dir=final_root_p.parent)
    )
    for relative, payload in pending_outputs.items():
        destination = pending_stage / Path(relative)
        if isinstance(payload, bytes):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
        else:
            write_json_fn(destination, payload)
    for relative, payload in final_outputs.items():
        write_json_fn(final_stage / Path(relative), payload)

    pending_manifest = pending_root_p.parent / ".kubejs-clean-manifest.json"
    final_manifest = final_root_p.parent / ".kubejs-final-manifest.json"
    pending_owned, pending_conflicts, stale_pending_removed = sync_managed_tree(
        pending_stage, pending_root_p, load_owned_files(pending_manifest)
    )
    final_owned, final_conflicts, stale_final_removed = sync_managed_tree(
        final_stage, final_root_p, load_owned_files(final_manifest)
    )
    atomic_write_json(pending_manifest, {"version": 1, "files": pending_owned})
    atomic_write_json(final_manifest, {"version": 1, "files": final_owned})
    shutil.rmtree(pending_stage, ignore_errors=True)
    shutil.rmtree(final_stage, ignore_errors=True)

    for relative in pending_conflicts:
        log_info_fn(f"[KubeJS-CLEAN] 保留未受管理或已修改的待翻譯檔：{relative}")
    for relative in final_conflicts:
        log_info_fn(f"[KubeJS-CLEAN] 保留未受管理或已修改的完成檔：{relative}")
    for conflict in provenance_conflicts:
        log_info_fn(
            "[KubeJS-CLEAN] 來源譯文衝突："
            f"{conflict['path']} key={conflict['key']} "
            f"type={conflict['kind']}；{conflict['decision']}"
        )

    log_info_fn(
        f"[KubeJS-CLEAN] 處理完畢！群組數: {len(groups)} | 產出待翻譯: {pending_lang_written} | 產出完成品: {merged_lang_written} | 複製其他檔案: {copied_other} | 保留舊繁中鍵: {previous_final_keys_preserved} | 清除過期待翻譯: {stale_pending_removed}"
    )

    return {
        "raw_root": str(raw_root),
        "pending_root": str(pending_root_p),
        "final_root": str(final_root_p),
        "groups": len(groups),
        "pending_lang_written": pending_lang_written,
        "merged_lang_written": merged_lang_written,
        "copied_other_jsons": copied_other,
        "previous_final_keys_preserved": previous_final_keys_preserved,
        "stale_pending_removed": stale_pending_removed,
        "stale_final_removed": stale_final_removed,
        "write_conflicts": len(pending_conflicts) + len(final_conflicts),
        "provenance_conflicts": provenance_conflicts,
    }
