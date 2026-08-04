"""translation_tool/core/lang_merge_pipeline.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, Dict

import orjson as json

from ..utils.log_unit import log_info, log_exception
from ..utils.text_processor import recursive_translate_dict, apply_replace_rules
from .lang_codec import dump_lang_text, parse_lang_text, pick_first_not_none
from .lang_merge_io import DirReader, quarantine_copy
from .lang_merge_zip_io import (
    _write_bytes_atomic,
    _write_text_atomic,
)
from .lang_merge_dict import (
    contains_cjk as _contains_cjk,
    is_pure_english as _is_pure_english,
)
from .lang_processing_format import dump_json_bytes


CJK_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df\U0002a700-\U0002ebef\U00030000-\U0003134f]"
)


@lru_cache(maxsize=4096)
def _contains_cjk_str(s: str) -> bool:
    """Memoized CJK check for string input (module-level)."""
    return bool(CJK_RE.search(s))


def _process_single_mod(
    reader,
    paths: Dict[str, str],
    rules: list,
    output_dir: str,
    must_translate_dir: str,
    errordata_dir: str | None = None,
    all_files_cache: list[str] | None = None,  # 2026-08-04: 預先算好的檔案列表
) -> Dict[str, Any]:
    """處理單一模組（mod）的語言合併流程。

    讀取 zh_cn / zh_tw / en_us lang 檔案，依據來源優先順序產生最終的 zh_tw.json，
    並將待翻譯（純英文）項目寫入 en_us.json 至 must_translate_dir。

    支援 ZIP 與資料夾兩種 reader。
    """

    # 2026-08-02 重構: _contains_cjk / has_any_text / is_pure_english
    # 從 _process_single_mod nested 抽出到 lang_merge_dict 模組,
    # 跟 Stage 2 共用同一個實作避免重複維護。
    # 在這裡只 import 別名,不重新定義 nested function。

    def _safe_read_lang_json(lang_key: str) -> Dict[str, Any]:
        """ """
        path = paths.get(lang_key)
        if not path:
            return {}

        try:
            if path.lower().endswith(".lang"):
                text = reader.read_text(path)

                bad_lines = []

                def on_error(line_no, raw, reason):
                    """記錄解析錯誤。"""
                    bad_lines.append((line_no, raw, reason))

                data = parse_lang_text(text, on_error=on_error)

                if bad_lines:
                    quarantine_copy(
                        reader=reader,
                        rel_path=path,
                        output_dir=output_dir,
                        reason="lang_partial_parse_error",
                        errordata_dir=errordata_dir,
                        extra_text="\n".join(
                            f"[line {n}] {reason}: {raw}"
                            for n, raw, reason in bad_lines
                        ),
                    )

                return data

            else:
                return reader.read_json(path)

        except Exception as e:
            quarantine_copy(
                reader=reader,
                rel_path=path,
                output_dir=output_dir,
                reason=f"lang_json_parse_failed: {e}",
                errordata_dir=errordata_dir,
            )
            return {}

    try:
        # =============================
        # 基本資訊
        # =============================
        mod_key = paths.get("zh_cn") or paths.get("zh_tw") or paths.get("en_us")
        mod_name = mod_key.split("/lang/")[0].split("/")[-1]
        log_prefix = f"處理語言模組 '{mod_name}': "

        # =============================
        # Step 1 — 讀取所有來源
        # =============================
        cn_data = _safe_read_lang_json("zh_cn")
        tw_src_data = _safe_read_lang_json("zh_tw")
        en_data = _safe_read_lang_json("en_us")

        # =============================
        # Step 2 — 決定輸出路徑
        # =============================
        base_path_hint = paths.get("zh_cn") or paths.get("zh_tw") or paths.get("en_us")
        if "/lang/" in base_path_hint:
            relative_tw_path = base_path_hint.split("/lang/")[0] + "/lang/zh_tw.json"
        else:
            relative_tw_path = os.path.join(mod_name, "lang", "zh_tw.json")

        # 自動偵測並剝離 ZIP 統一包裝前綴（任何名稱皆適用）
        # 讀取 ZIP 時用原始路徑，只在輸出路徑建構時剝離
        # 已知標準資源目錄（這些目錄名稱本身就是有意義的結構，不剝離）
        _STANDARD_RESOURCE_DIRS = {"assets", "book", "patchouli_books", "resources"}
        # 2026-08-04 性能優化: 用 caller 預先算好的 all_files_cache
        _all_names = all_files_cache if all_files_cache is not None else reader.list_all()
        _wp = None
        if _all_names:
            _tops = set(n.replace("\\", "/").split("/")[0] for n in _all_names if n.replace("\\", "/").split("/")[0])
            if len(_tops) == 1:
                _candidate = list(_tops)[0]
                if _candidate not in _STANDARD_RESOURCE_DIRS:
                    _wp = _candidate + "/"

        def _strip(p):
            return p[len(_wp):] if _wp and p.startswith(_wp) else p

        final_output_rel = _strip(relative_tw_path)
        final_output_path = os.path.join(output_dir, final_output_rel)
        target_has_tw = os.path.exists(final_output_path)

        # =============================
        # Step 3 — 建立 final_tw
        # =============================
        # 第一優先：已存在 output zh_tw.json
        if target_has_tw:
            try:
                with open(final_output_path, "rb") as f:
                    final_tw = json.loads(f.read())
            except Exception:
                final_tw = {}
        else:
            final_tw = {}

        # =============================
        # Step 4 — 逐條判斷合併來源（重點修改）
        # =============================
        # 2026-08-02 重構:把原本 60+ 行的 merge loop 換成單一 helper 函式
        # merge_lang_dicts 是從 _process_single_mod 拆出來的純函式,
        # Stage 2 (merge_extracted_to_assets) 會重用同一 helper,
        # 確保 Stage 1 跟 Stage 2 邏輯一致。
        from .lang_merge_dict import merge_lang_dicts

        final_tw, pending = merge_lang_dicts(
            cn_data=cn_data,
            tw_src_data=tw_src_data,
            en_data=en_data,
            existing_tw=final_tw,
            rules=rules,
            apply_replace_rules=apply_replace_rules,
            recursive_translate_dict=recursive_translate_dict,
            contains_cjk=_contains_cjk,
            is_pure_english=_is_pure_english,
            is_from_output_dir=target_has_tw,
        )

        # =============================
        # Step 5 — 寫入 pending.json
        # =============================
        # pending 路徑與 final_output_rel 使用相同的已剝離前綴邏輯
        pending_rel = final_output_rel.replace("zh_tw.json", "en_us.json")
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

        # =============================
        # Step 6 — 輸出 zh_tw.json
        # =============================

        # if final_tw:
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

        log_info(f"{log_prefix}完成，pending 條目: {pending_count}")
        return {
            "success": True,
            # "log": f"{log_prefix}完成，pending 條目: {pending_count}",
            "pending_count": pending_count,
        }

    except Exception as exc:
        log_exception(f"{log_prefix}處理失敗: {exc}")
        return {
            "success": False,
            # "log": f"{log_prefix}處理失敗: {exc}",
            "error": True,
        }
