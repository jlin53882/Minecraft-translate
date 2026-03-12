"""translation_tool/core/lang_merge_pipeline.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import logging
import os
import re
import zipfile
from typing import Any, Dict

import orjson as json

from ..utils.text_processor import apply_replace_rules, recursive_translate_dict
from .lang_codec import dump_lang_text, parse_lang_text, pick_first_not_none
from .lang_merge_zip_io import _read_json_from_zip, _read_text_from_zip, _write_bytes_atomic, _write_text_atomic, quarantine_copy_from_zip
from .lang_processing_format import dump_json_bytes

logger = logging.getLogger(__name__)

def _process_single_mod(
    zf: zipfile.ZipFile,
    paths: Dict[str, str],
    rules: list,
    output_dir: str,
    must_translate_dir: str
) -> Dict[str, Any]:

    """`_process_single_mod`
    
    用途：
    - 處理此函式的主要流程（細節以程式碼為準）。
    - 主要包裝/呼叫：`compile`
    
    參數：
    - 依函式簽名。
    
    回傳：
    - 依實作回傳值（請見函式內 return path）。
    """
    CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

    #def contains_cjk(s: str) -> bool:
    #    """是否包含任意 CJK（中文/日文/韓文）"""
    #    return isinstance(s, str) and CJK_RE.search(s) is not None
#
    #def is_pure_english(s: str) -> bool:
    #    """判斷是否為純英文（不包含 CJK）"""
    #    return isinstance(s, str) and not contains_cjk(s)


    def contains_cjk(v: Any) -> bool:
        """是否包含任意 CJK（支援 str / list / dict 遞迴）"""
        if isinstance(v, str):
            return CJK_RE.search(v) is not None
        if isinstance(v, list):
            return any(contains_cjk(x) for x in v)
        if isinstance(v, dict):
            return any(contains_cjk(x) for x in v.values())
        return False

    def has_any_text(v: Any) -> bool:
        """結構內是否至少有一段可用的文字（避免空結構被當 pending）"""
        if isinstance(v, str):
            return v.strip() != ""
        if isinstance(v, list):
            return any(has_any_text(x) for x in v)
        if isinstance(v, dict):
            return any(has_any_text(x) for x in v.values())
        return False

    def is_pure_english(v: Any) -> bool:
        """
        判斷是否為「不包含 CJK」的內容（支援結構）。
        - 需要至少有一段字串
        - 且所有字串都不含 CJK
        """
        if not has_any_text(v):
            return False
        return not contains_cjk(v)



    def _safe_read_lang_json(lang_key: str) -> Dict[str, Any]:
        """`_safe_read_lang_json`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`get`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - 依實作回傳值（請見函式內 return path）。
        """
        path = paths.get(lang_key)
        if not path:
            return {}

        try:
            if path.lower().endswith(".lang"):
                text = _read_text_from_zip(zf, path)

                bad_lines = []

                def on_error(line_no, raw, reason):
                    """`on_error`
                    
                    用途：
                    - 處理此函式的主要流程（細節以程式碼為準）。
                    - 主要包裝/呼叫：`append`
                    
                    參數：
                    - 依函式簽名。
                    
                    回傳：
                    - None
                    """
                    bad_lines.append((line_no, raw, reason))

                data = parse_lang_text(text, on_error=on_error)

                if bad_lines:
                    quarantine_copy_from_zip(
                        zf=zf,
                        zip_path=path,
                        output_dir=output_dir,
                        reason="lang_partial_parse_error",
                        extra_text="\n".join(
                            f"[line {n}] {r}: {l}"
                            for n, l, r in bad_lines
                        )
                    )

                return data

            else:
                # JSON 是結構化的，只要壞 → 整檔隔離
                return _read_json_from_zip(zf, path)

        except Exception as e:
            quarantine_copy_from_zip(
                zf=zf,
                zip_path=path,
                output_dir=output_dir,
                reason=f"lang_json_parse_failed: {e}"
            )
            return {}


    try:
        #=============================
        # 基本資訊
        #=============================
        mod_key = paths.get("zh_cn") or paths.get("zh_tw") or paths.get("en_us")
        mod_name = mod_key.split("/lang/")[0].split("/")[-1]
        log_prefix = f"處理語言模組 '{mod_name}': "

        #=============================
        # Step 1 — 讀取所有來源
        #=============================
        cn_data = _safe_read_lang_json("zh_cn")
        tw_src_data = _safe_read_lang_json("zh_tw")
        en_data = _safe_read_lang_json("en_us")

        #=============================
        # Step 2 — 決定輸出路徑
        #=============================
        base_path_hint = paths.get("zh_cn") or paths.get("zh_tw") or paths.get("en_us")
        if "/lang/" in base_path_hint:
            relative_tw_path = base_path_hint.split("/lang/")[0] + "/lang/zh_tw.json"
        else:
            relative_tw_path = os.path.join(mod_name, "lang", "zh_tw.json")

        final_output_path = os.path.join(output_dir, relative_tw_path)
        target_has_tw = os.path.exists(final_output_path)

        #=============================
        # Step 3 — 建立 final_tw
        #=============================
        # 第一優先：已存在 output zh_tw.json
        if target_has_tw:
            try:
                with open(final_output_path, "rb") as f:
                    final_tw = json.loads(f.read())
            except:
                final_tw = {}
        else:
            final_tw = {}

        #=============================
        # Step 4 — 逐條判斷合併來源（重點修改）
        #=============================
        pending = {}

        # 所有 key 的集合
        all_keys = set(cn_data.keys()) | set(tw_src_data.keys()) | set(en_data.keys())

        for key in all_keys:
            # 1. 若 final_tw 已有人工翻譯（含 CJK），不覆蓋
            #if key in final_tw and contains_cjk(final_tw[key]):
            #    continue

            # -----------------------------
            # 人工 zh_tw 保護（來源感知）
            # -----------------------------
            # 只有「已存在於 output_dir 的 zh_tw」才視為人工翻譯並保護
            # 外部 zip 內的 zh_tw（tw_src_data）仍允許再處理（套規則）

            is_from_output_dir = (
                key in final_tw
                and target_has_tw          # 代表 output_dir 已存在 zh_tw.json
            )

            if is_from_output_dir and contains_cjk(final_tw.get(key, "")):
                # ✔ 人工翻譯 → 不動
                continue

            tw_val = tw_src_data.get(key)
            cn_val = cn_data.get(key)
            en_val = en_data.get(key)

            # 2. zh_tw（ZIP）若含中文 → 優先使用
            #if contains_cjk(tw_val):
            #    #final_tw[key] = tw_val # 直接使用 ZIP 內的 zh_tw
            #    final_tw[key] = apply_replace_rules(tw_val, rules) # 進行規則處理
            #    continue

            if contains_cjk(tw_val):
                if isinstance(tw_val, str):
                    final_tw[key] = apply_replace_rules(tw_val, rules)
                else:
                    final_tw[key] = recursive_translate_dict(tw_val, rules)
                continue

            # 3. zh_cn 若含中文 → 用 S2TW 翻譯
            if contains_cjk(cn_val):
                final_tw[key] = recursive_translate_dict(cn_val, rules)
                continue

            # 4. zh_tw 與 zh_cn 皆為英文 → 視為未翻完，寫入 pending
            #english_source = en_val or cn_val or tw_val
            english_source = pick_first_not_none(en_val, cn_val, tw_val)
            if english_source is None:
                english_source = ""
            
            # -----------------------------
            # 過濾空字串（來源本來就是 ""）
            # -----------------------------
            if isinstance(english_source, str) and english_source.strip() == "":
                # 空字串不是待翻譯內容，直接跳過
                continue            
                
            if is_pure_english(english_source):
                pending[key] = english_source
                continue

            # 5. fallback 保護
            if english_source is None:
                english_source = ""
            final_tw.setdefault(key, english_source)

        #=============================
        # Step 5 — 寫入 pending.json
        #=============================
        pending_rel = relative_tw_path.replace("zh_tw.json", "en_us.json")
        pending_path = os.path.join(must_translate_dir, pending_rel)
        os.makedirs(os.path.dirname(pending_path), exist_ok=True)

        if pending:
            # ⭐ 新增排序：讓 diff 更乾淨 ⭐
            pending = dict(sorted(pending.items(), key=lambda item: item[0]))
            _write_bytes_atomic(pending_path, dump_json_bytes(pending))
            pending_count = len(pending)
        else:
            if os.path.exists(pending_path):
                os.remove(pending_path)
            pending_count = 0

        #=============================
        # Step 6 — 輸出 zh_tw.json
        #=============================

        #if final_tw:
        #    # ⭐⭐ 新增：讓 key 按英文字母排序 ⭐⭐
        #    final_tw = dict(sorted(final_tw.items(), key=lambda item: item[0]))
        #    os.makedirs(os.path.dirname(final_output_path), exist_ok=True)
        #    _write_bytes_atomic(final_output_path, dump_json_bytes(final_tw))
                
        if final_tw:
            # 是否為 lang 格式（依原始檔案）
            is_lang_format = base_path_hint.lower().endswith(".lang")

            # ⭐ 先排序（JSON 與 lang 都要）
            final_tw = dict(sorted(final_tw.items(), key=lambda item: item[0]))

            # ⭐ 根據格式決定最終輸出路徑
            if is_lang_format:
                final_output_path = os.path.splitext(final_output_path)[0] + ".lang"
            else:
                final_output_path = os.path.splitext(final_output_path)[0] + ".json"

            # ⭐ 用最終路徑建立資料夾
            os.makedirs(os.path.dirname(final_output_path), exist_ok=True)

            # ⭐ 寫入
            if is_lang_format:
                _write_text_atomic(final_output_path, dump_lang_text(final_tw))
            else:
                _write_bytes_atomic(final_output_path, dump_json_bytes(final_tw))

        logger.info(f"{log_prefix}完成，pending 條目: {pending_count}")
        return {
            "success": True,
            #"log": f"{log_prefix}完成，pending 條目: {pending_count}",
            "pending_count": pending_count
        }

    except Exception as exc:
        logger.exception(f"{log_prefix}處理失敗: {exc}")
        return {
            "success": False,
            #"log": f"{log_prefix}處理失敗: {exc}",
            "error": True
        }
