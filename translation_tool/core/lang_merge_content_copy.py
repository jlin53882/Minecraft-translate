"""translation_tool/core/lang_merge_content_copy.py 模組。

用途：語言檔案的內容複製與處理功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import os
import re
import shutil
import zipfile
from typing import Any, Callable, Dict, List

from ..utils.log_unit import log_info, log_warning, log_error, log_debug


# ----------------------------------------------------------------------
# Helper: Patchouli 翻譯有效性 ratio 計算
# ----------------------------------------------------------------------
def _compute_patchouli_lang_effectiveness(
    zf,
    book_root: str,
    threshold: float = 0.5,
    json_module=None,
) -> dict[str, bool]:
    """Compute effective translation status for zh_tw and zh_cn in a book_root.

    Returns: {"zh_tw": bool, "zh_cn": bool}
    A language is "effective" when its ratio of effective-CJK files >= threshold.

    "Effective CJK file" definition:
    - JSON: parse, recursively extract strings, if CJK chars / total chars >= 0.5 → effective
    - MD/TXT: read as text, if CJK char ratio >= 0.5 → effective
    """
    import re as _re

    CJK_RE = _re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
    TEXT_EXTS = {".json", ".md", ".txt"}

    result: dict[str, bool] = {"zh_tw": False, "zh_cn": False}

    for lang in ("zh_tw", "zh_cn"):
        prefix = book_root + lang + "/"
        text_files: list[str] = []
        for name in zf.namelist():
            if name.startswith(prefix):
                ext = os.path.splitext(name)[1].lower()
                if ext in TEXT_EXTS:
                    text_files.append((name, ext))

        if not text_files:
            continue

        effective_count = 0
        for fname, ext in text_files:
            try:
                raw = zf.read(fname).decode("utf-8", errors="replace")
                if ext == ".json":
                    try:
                        data = json_module.loads(raw)
                        strings = _extract_all_strings(data)
                    except Exception:
                        continue
                else:
                    strings = [raw]

                total = sum(len(s) for s in strings if isinstance(s, str))
                if total == 0:
                    continue
                cjk_chars = sum(len(CJK_RE.findall(s)) for s in strings if isinstance(s, str))
                if total > 0 and cjk_chars / total >= 0.5:
                    effective_count += 1
            except Exception:
                continue

        ratio = effective_count / len(text_files) if text_files else 0
        result[lang] = ratio >= threshold

    return result


def _extract_all_strings(data) -> list[str]:
    """Recursively extract all string values from a dict/list."""
    strings: list[str] = []
    if isinstance(data, str):
        strings.append(data)
    elif isinstance(data, dict):
        for v in data.values():
            strings.extend(_extract_all_strings(v))
    elif isinstance(data, list):
        for item in data:
            strings.extend(_extract_all_strings(item))
    return strings


def process_content_or_copy_file_impl(
    zf: zipfile.ZipFile,
    input_path: str,
    rules: list,
    output_dir: str,
    *,
    only_process_lang: bool = False,
    all_files_cache: List[str] | None = None,
    load_config_fn: Callable[[], dict],
    recursive_translate_dict_fn: Callable[[Any, list], Any],
    get_text_processor_fn: Callable[[str], Any],
    read_text_from_zip_fn: Callable[[zipfile.ZipFile, str], str],
    write_bytes_atomic_fn: Callable[[str, bytes], Any],
    write_text_atomic_fn: Callable[[str, str], Any],
    quarantine_copy_from_zip_fn: Callable[..., Any],
    normalize_patchouli_book_root_fn: Callable[[str], str],
    patch_localized_content_json_fn: Callable[..., Dict[str, Any]],
    json_module,
) -> Dict[str, Any]:
    """處理非標準 lang JSON / patchouli / 純文字內容的 copy-or-patch 流程。"""
    normalized_path = input_path.lower().replace("\\", "/")
    log_debug(f"[Patchouli DEBUG] 原始 input_path = {input_path}")
    log_debug(f"[Patchouli DEBUG] normalized_path(初始) = {normalized_path}")

    assets_idx = normalized_path.find("/assets/")
    log_debug(f"[Patchouli DEBUG] assets_idx = {assets_idx}")
    if assets_idx != -1:
        normalized_path = normalized_path[assets_idx + 1 :]
    log_debug(f"[Patchouli DEBUG] normalized_path(裁切後) = {normalized_path}")

    # --- v3 新增：zh_cn skip logic (在 early return 判斷之前，先做全局開關檢查) ---
    merger_cfg = load_config_fn().get("lang_merger", {})
    process_zh_cn = merger_cfg.get("process_zh_cn_files", True)
    skip_zh_cn_when_only_lang = merger_cfg.get("skip_zh_cn_when_only_process_lang", False)
    # 全局關閉 zh_cn 時，直接跳過所有 zh_cn 內容檔案（非 lang 也要檢查）
    norm_lower = input_path.lower().replace("\\", "/")
    if not process_zh_cn:
        if "/zh_cn/" in norm_lower or "/zh_cn." in norm_lower:
            return {"success": True, "log": None}

    if only_process_lang:
        if "/lang/" not in f"/{normalized_path}":
            return {"success": True, "log": None}
        file_stem = os.path.splitext(os.path.basename(normalized_path))[0].lower()
        if file_stem not in ["zh_cn", "zh_tw", "en_us"]:
            return {"success": True, "log": None}
        # 只處理 lang + zh_cn 模式下，額外跳過 zh_cn.lang/zh_cn.json
        if not process_zh_cn:
            return {"success": True, "log": None}
        if skip_zh_cn_when_only_lang:
            if "/lang/" in norm_lower and ("zh_cn.json" in norm_lower or "zh_cn.lang" in norm_lower):
                return {"success": True, "log": None}

    def get_patchouli_book_root(path: str):
        p = path.replace("\\", "/").lower()
        if not p.startswith("/"):
            p = "/" + p
        idx = p.find("/assets/")
        if idx == -1:
            return None
        p_sub = p[idx + 1 :]
        patchouli_dirs = (
            load_config_fn().get("lm_translator", {}).get("patchouli", {}).get("dir_names", ["patchouli_books"])
        )
        if not isinstance(patchouli_dirs, list):
            patchouli_dirs = [patchouli_dirs]
        lang_dirs = {"zh_cn", "zh_tw", "en_us"}
        for dir_name in patchouli_dirs:
            marker = f"/{dir_name}/"
            if marker in p_sub:
                parts = p_sub.split(marker, 1)
                rest = parts[1].lstrip("/")
                first = rest.split("/", 1)[0] if rest else ""
                if first in lang_dirs:
                    book_root = parts[0] + marker
                    return (book_root, dir_name)
                book_id = first
                book_root = parts[0] + marker + book_id + "/"
                return (book_root, dir_name)
        return None

    hit = get_patchouli_book_root(normalized_path)
    book_root, matched_dir_name = hit if hit else (None, None)

    if book_root:
        has_cn_or_tw = False
        if all_files_cache:
            has_cn_or_tw = any(
                n.startswith(book_root) and ("/zh_cn/" in n or "/zh_tw/" in n)
                for n in all_files_cache
            )

        # --- v3 ratio-based patchouli skip ---
        _allow_zh_cn_for_skip = load_config_fn().get("lang_merger", {}).get(
            "patchouli_skip_en_us_when_zh_cn_exists", False
        )
        _effective_threshold = load_config_fn().get("lang_merger", {}).get(
            "patchouli_effective_translation_threshold", 0.5
        )

        _patchouli_effective = {}
        if book_root:
            _patchouli_effective = _compute_patchouli_lang_effectiveness(
                zf, book_root, threshold=_effective_threshold, json_module=json_module
            )

        has_effective_zh_tw = _patchouli_effective.get("zh_tw", False)
        has_effective_zh_cn = _patchouli_effective.get("zh_cn", False)

        if has_cn_or_tw:
            if normalized_path.lower().endswith("/en_us/...") or "/en_us/" in normalized_path.lower():
                should_skip = has_effective_zh_tw or (_allow_zh_cn_for_skip and has_effective_zh_cn)
                if should_skip:
                    return {"success": True, "log": f"[Patchouli] 跳過已有有效翻譯的英文原件: {normalized_path}"}
        # --- end v3 ratio-based patchouli skip ---

        rel_path = normalized_path[len(book_root) :]
        rel_low = rel_path.lower()
        normalized_root = normalize_patchouli_book_root_fn(book_root).strip("/")
        pending_name = load_config_fn().get("lang_merger", {}).get("pending_folder_name", "待翻譯")
        patchouli_dirs = (
            load_config_fn().get("lm_translator", {}).get("patchouli", {}).get("dir_names", ["patchouli_books"])
        )
        patchouli_root_dir = matched_dir_name if isinstance(patchouli_dirs, list) and patchouli_dirs else patchouli_dirs

        if has_cn_or_tw:
            if rel_low.startswith("en_us/"):
                should_skip = has_effective_zh_tw or (_allow_zh_cn_for_skip and has_effective_zh_cn)
                if should_skip:
                    return {"success": True, "log": f"[Patchouli] 跳過已有有效翻譯的英文原件: {normalized_path}"}
            if rel_low.startswith("zh_cn/"):
                rel_path = "zh_tw/" + rel_path[len("zh_cn/") :]
            elif rel_low.startswith("zh_tw/"):
                rel_path = rel_path
            target = os.path.join(output_dir, patchouli_root_dir, normalized_root, rel_path)
            action_log = "轉換中文化"
        else:
            target = os.path.join(output_dir, patchouli_root_dir, pending_name, normalized_root, rel_path)
            action_log = "歸檔至待翻譯"

        os.makedirs(os.path.dirname(target), exist_ok=True)
        ext = os.path.splitext(input_path)[1].lower()
        if ext in [".json", ".md", ".txt"]:
            try:
                raw_text = read_text_from_zip_fn(zf, input_path)
                tw_content = recursive_translate_dict_fn(raw_text, rules)
                with open(target, "w", encoding="utf-8") as f:
                    f.write(tw_content)
            except Exception as e:
                log_error(f"[Patchouli] 寫入失敗: {e}")
                with zf.open(input_path) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            with zf.open(input_path) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        return {"success": True, "log": f"[Patchouli] {action_log}: {target}"}

    log_prefix = f"處理內容檔案 '{input_path}':"
    file_name = os.path.basename(input_path)
    ext = os.path.splitext(file_name)[1].lower()
    is_path_localized = "zh_cn/" in normalized_path
    is_filename_localized = re.search(
        r"zh_cn.*?\.(lang|md|txt|snbt|json|properties|json5|gui|hl)$",
        file_name,
        re.IGNORECASE,
    ) is not None
    is_localized_cn_file = is_path_localized or is_filename_localized
    force_s2tw_extensions = {".md", ".json5", ".gui", ".lang", ".snbt", ".txt", ".properties", ".hl"}
    is_forced_s2tw = ext in force_s2tw_extensions

    log_debug(f"DEBUG: 進入處理函數，檔案: {input_path}")
    log_debug(f"DEBUG: 檔案 '{input_path}' 是否為本地化 (zh_cn/ 或 zh_cn.*.ext): {is_localized_cn_file}")
    log_debug(f"DEBUG: 檔案 '{input_path}' 是否為強制 S2TW: {is_forced_s2tw}")

    tw_path = input_path
    if is_localized_cn_file:
        if is_path_localized:
            tw_path = input_path.replace("\\", "/").replace("zh_cn/", "zh_tw/")
        tw_path = re.sub(r"zh_cn(\..*)$", r"zh_tw\1", tw_path, flags=re.IGNORECASE)
        final_output_path = os.path.join(output_dir, tw_path)
        os.makedirs(os.path.dirname(final_output_path), exist_ok=True)
    else:
        final_output_path = os.path.join(output_dir, tw_path)

    output_dir_path = os.path.dirname(final_output_path)
    os.makedirs(output_dir_path, exist_ok=True)

    try:
        if not is_localized_cn_file:
            if ext == ".json":
                try:
                    text = read_text_from_zip_fn(zf, input_path)
                    source_data = json_module.loads(text)
                except Exception as e:
                    error_detail = f"Exception: {type(e).__name__}\nMessage: {str(e)}\nPath: {input_path}"
                    lang = "unknown"
                    for possible_lang in ["zh_cn", "zh_tw", "en_us"]:
                        if possible_lang in normalized_path:
                            lang = possible_lang
                            break
                    quarantine_copy_from_zip_fn(
                        zf=zf,
                        zip_path=input_path,
                        output_dir=output_dir,
                        reason=f"JSON解析失敗 (語言: {lang})",
                        extra_text=error_detail,
                    )
                    log_warning(f"{log_prefix} JSON 無法解析，已跳過並隔離: {e}")
                    return {"success": True}

                if "/lang/" in normalized_path and file_name.lower() == "zh_tw.json":
                    if os.path.exists(final_output_path):
                        try:
                            with open(final_output_path, "rb") as f:
                                existing = json_module.loads(f.read())
                        except Exception:
                            existing = {}
                    else:
                        existing = {}
                    final_data = dict(existing)
                    for k, v in source_data.items():
                        if k not in final_data:
                            final_data[k] = recursive_translate_dict_fn(v, rules)
                    log_message = f"{log_prefix} 非本地化 zh_tw JSON 增量補缺 (新內容已 S2TW) 與格式化完成。"
                else:
                    final_data = recursive_translate_dict_fn(source_data, rules)
                    log_message = f"{log_prefix} JSON 檔案已 S2TW 轉換、格式化並複製完成。"

                final_bytes = json_module.dumps(final_data, option=json_module.OPT_INDENT_2)
                should_write = True
                if os.path.exists(final_output_path):
                    try:
                        with open(final_output_path, "rb") as f:
                            existing_bytes = f.read()
                        existing_normalized_data = json_module.loads(existing_bytes)
                        existing_normalized_bytes = json_module.dumps(existing_normalized_data, option=json_module.OPT_INDENT_2)
                        if existing_normalized_bytes == final_bytes:
                            should_write = False
                    except Exception:
                        should_write = True
                if should_write:
                    write_bytes_atomic_fn(final_output_path, final_bytes)
                    log_info(log_message)
                    return {"success": True}
                log_debug(f"{log_prefix} JSON 檔案內容和格式一致，略過寫入。")
                return {"success": True, "log": None}

            if ext == ".png":
                with zf.open(input_path) as src, open(final_output_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                log_debug(f"DEBUG: 本地化圖片檔案 {file_name} 複製完成: {final_output_path}")
                log_info(f"{log_prefix} PNG 檔案直接複製。")
                return {"success": True}

            if ext == ".mcmeta":
                with zf.open(input_path) as src, open(final_output_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                log_info(f"{log_prefix} .mcmeta 檔案複製完成: {final_output_path}")
                return {"success": True}

            processor = get_text_processor_fn(ext)
            raw = read_text_from_zip_fn(zf, input_path)
            raw = raw.replace("\r\n", "\n").replace("\r", "\n")
            if processor:
                tw_content = processor(raw, recursive_translate_dict_fn, rules, input_path)
            else:
                tw_content = recursive_translate_dict_fn(raw, rules)
            should_write = True
            if os.path.exists(final_output_path):
                try:
                    with open(final_output_path, "r", encoding="utf-8") as f:
                        existing_content = f.read().replace("\r\n", "\n").replace("\r", "\n")
                    if existing_content == tw_content:
                        should_write = False
                except Exception:
                    should_write = True
            if should_write:
                write_text_atomic_fn(final_output_path, tw_content)
                log_info(f"{log_prefix} 非本地化純文字檔案 S2TW 轉換完成。")
                return {"success": True}
            log_debug(f"{log_prefix} 檔案內容一致，略過寫入。")
            return {"success": True, "log": None}

        if ext == ".png":
            try:
                log_msg = None
                if not os.path.exists(final_output_path):
                    with zf.open(input_path) as src, open(final_output_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    log_msg = f"{log_prefix} 圖片檔案 (.png) 複製完成 (新檔案)。"
                else:
                    input_content = zf.read(input_path)
                    with open(final_output_path, "rb") as f:
                        output_content = f.read()
                    if input_content != output_content:
                        with open(final_output_path, "wb") as dst:
                            dst.write(input_content)
                        log_msg = f"{log_prefix} 圖片檔案 (.png) 內容不同，執行覆蓋。"
                    else:
                        log_msg = f"{log_prefix} 圖片檔案 (.png) 內容相同，跳過複製。"
                log_debug(f"DEBUG: 本地化圖片檔案 {file_name} 處理完成: {final_output_path}")
                log_info(log_msg)
                return {"success": True}
            except (zipfile.BadZipFile, EOFError) as e:
                log_error(f"跳過損毀的 ZIP 內檔案 {input_path}: {e}")
                return {"success": False, "error": True}
            except Exception as e:
                log_error(f"處理 {input_path} 時發生未預期錯誤: {e}")
                return {"success": False, "error": True}

        if ext == ".json" and is_localized_cn_file:
            return patch_localized_content_json_fn(zf, input_path, final_output_path, rules, log_prefix, output_dir)

        if ext == ".mcmeta":
            with zf.open(input_path) as src, open(final_output_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            log_debug(f"DEBUG: 本地化 .mcmeta 檔案 {file_name} 複製完成: {final_output_path}")
            log_info(f"{log_prefix} 本地化檔案類型 ({ext}) 已被排除 S2TW 轉換，執行直接複製。")
            return {"success": True}

        processor = get_text_processor_fn(ext)
        if processor:
            raw = read_text_from_zip_fn(zf, input_path)
            raw = raw.replace("\r\n", "\n").replace("\r", "\n")
            tw_content = processor(raw, recursive_translate_dict_fn, rules, input_path)
            should_write = True
            log_msg = None
            if os.path.exists(final_output_path):
                try:
                    with open(final_output_path, "r", encoding="utf-8") as f:
                        existing_content = f.read().replace("\r\n", "\n").replace("\r", "\n")
                    if existing_content == tw_content:
                        should_write = False
                        log_debug(f"{log_prefix} 內容檔案 ({ext}) S2TW 轉換後內容無變動，略過寫入。")
                except Exception:
                    should_write = True
            if should_write:
                write_text_atomic_fn(final_output_path, tw_content)
                if is_forced_s2tw and not is_localized_cn_file:
                    log_msg = f"{log_prefix} 非本地化檔案 ({ext}) S2TW 結構化轉換完成。"
                else:
                    log_msg = f"{log_prefix} 內容檔案 ({ext}) S2TW 結構化轉換完成。"
            log_debug(f"DEBUG: 檔案 {file_name} S2TW 處理完成。")
            log_info(log_msg)
            return {"success": True}

        with zf.open(input_path) as src, open(final_output_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        log_info(f"{log_prefix} 未知本地化檔案類型 ({ext}) 直接複製完成。")
        return {"success": True}
    except Exception as exc:
        log_error(f"處理內容檔案 {input_path} 時發生錯誤: {exc}", exc_info=True)
        return {"success": False, "error": True}
