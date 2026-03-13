"""translation_tool/plugins/ftbquests/ftbquests_snbt_inject.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# ftbquests_snbt_inject.py
# FTB Quests SNBT 注入工具：將已整理好的 zh_tw JSON 寫回 quests/lang/zh_tw/*.snbt

from __future__ import annotations

import os
import shutil
from typing import Dict, Any, Tuple, Optional, List
from collections import defaultdict

import orjson
import ftb_snbt_lib as snbt
from ftb_snbt_lib.tag import Compound, List as SnbtList  # 避免跟 typing.List 混淆
from ...utils.text_processor import (
    convert_snbt_tree_inplace,  # 轉換.snbt 資料夾檔案內容（就地修改）。
    convert_snbt_file_inplace,  # 轉換.snbt（或任何純文字檔）內容。
    load_replace_rules,  # 載入替換規則。
)
from ...utils.config_manager import load_config

# 導入我們自訂的日誌工具
from translation_tool.utils.log_unit import (
    log_info,
    log_error,
    log_warning,
    log_debug,
)

def _normalize_config_dir(path: str) -> str:
    """
    容錯：避免出現 .../config/config 這種重複路徑。
    """
    if not path:
        return path
    norm = os.path.normpath(path)
    base = os.path.basename(norm).lower()
    parent = os.path.basename(os.path.dirname(norm)).lower()
    if base == "config" and parent == "config":
        return os.path.dirname(norm)
    return norm

def _load_json_dict(path: str) -> dict:
    """

    """
    if not os.path.isfile(path):
        return {}
    with open(path, "rb") as f:
        return orjson.loads(f.read())

def split_lang_by_source_file(lang_map: dict) -> Dict[str, Dict[str, str]]:
    """
    將 extractor 產出的扁平 key 拆回來源檔：
      - "xxx.snbt|some.key" -> 檔名=xxx.snbt, inner_key=some.key
      - "some.key" -> 放到 "_default"（代表沒有來源檔名資訊）
    回傳:
      {
        "aaa.snbt": {"k1":"v1", ...},
        "bbb.snbt": {...},
        "_default": {...}
      }
    """
    out: Dict[str, Dict[str, str]] = {}
    for k, v in lang_map.items():
        if not (
            isinstance(v, str)
            or (isinstance(v, list) and all(isinstance(x, str) for x in v))
        ):
            continue
        if "|" in k:
            filename, inner_key = k.split("|", 1)
        else:
            filename, inner_key = "_default", k
        out.setdefault(filename, {})[inner_key] = v
    return out

def _walk_and_copy_template(template_dir: str, zh_tw_dir: str) -> int:
    """
    把 template_dir 下所有 .snbt 複製到 zh_tw_dir（保留相對路徑結構）
    回傳複製的檔案數
    """
    copied = 0
    os.makedirs(zh_tw_dir, exist_ok=True)

    for root, _, files in os.walk(template_dir):
        for fn in files:
            if not fn.lower().endswith(".snbt"):
                continue
            src = os.path.join(root, fn)
            rel = os.path.relpath(src, template_dir)
            dst = os.path.join(zh_tw_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1

    return copied

def walk_and_copy_all_snbt(src_root_dir: str, dst_root_dir: str) -> int:
    """
    把 src_root_dir 下所有 .snbt 複製到 dst_root_dir（保留相對路徑）
    回傳複製數量
    """
    copied = 0
    os.makedirs(dst_root_dir, exist_ok=True)

    for root, _, files in os.walk(src_root_dir):
        for fn in files:
            if not fn.lower().endswith(".snbt"):
                continue
            src = os.path.join(root, fn)
            rel = os.path.relpath(src, src_root_dir)
            dst = os.path.join(dst_root_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1

    return copied

def _read_snbt(path: str) -> Compound | None:
    """

    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return snbt.load(f)
    except Exception as e:
        log_error("SNBT 讀取失敗: %s -> %s", path, e)
        return None

def _write_snbt(path: str, root: Compound) -> None:
    """

    回傳：None
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(snbt.dumps(root))

def patch_lang_snbt_file(
    src_path: str,
    dst_path: str,
    updates: Dict[str, Any],
    *,
    return_details: bool = False,  # ✅ 新增：要不要回傳詳細資訊
) -> Tuple[int, int] | Tuple[int, int, Dict[str, Any]]:
    """
    以 src_path 的結構為準，將 updates 覆蓋到 dst_path（dst 通常是複製的模板檔）
    - 只更新模板內存在的 key（避免亂塞 key）
    - 支援：
      * 模板值是 String：updates 必須是 str
      * 模板值是 List[String]：updates 可以是 list[str] 或 str（用 \\n 分割回 list）

    回傳：
      - 預設： (changed_keys, candidate_keys)
      - return_details=True： (changed_keys, candidate_keys, details)

    details 會包含：
      - total_updates: updates 總數
      - missing_in_template: 模板不存在的 keys
      - skipped_type_mismatch: 型別不符合而跳過的 keys
      - unchanged_keys: 找到但內容相同（所以不算 changed）的 keys  ✅ 你要找的常常是這個
      - changed_keys: 有實際寫入變更的 keys
    """
    src_root = _read_snbt(src_path)
    if not src_root:
        if return_details:
            return (
                0,
                0,
                {
                    "total_updates": len(updates),
                    "missing_in_template": [],
                    "skipped_type_mismatch": [],
                    "unchanged_keys": [],
                    "changed_keys": [],
                },
            )
        return 0, 0

    dst_root = _read_snbt(dst_path)
    if not dst_root:
        dst_root = src_root

    changed = 0
    candidates = 0

    # ✅ 詳細統計
    missing_in_template: List[str] = []
    skipped_type_mismatch: List[str] = []
    unchanged_keys: List[str] = []
    changed_keys: List[str] = []

    def _list_to_py(v):
        """

    
        """
        if isinstance(v, SnbtList):
            out = []
            for e in v:
                out.append(str(e) if isinstance(e, snbt.String) else str(e))
            return out
        return None

    for k, new_val in updates.items():
        # --- 模板沒有這個 key：真正「缺少」 ---
        if k not in src_root:
            missing_in_template.append(k)
            continue

        candidates += 1
        template_val = src_root.get(k)
        old_val = dst_root.get(k)

        # --- 1) 模板是字串 ---
        if isinstance(template_val, snbt.String):
            if not isinstance(new_val, str):
                skipped_type_mismatch.append(k)
                continue

            old_s = str(old_val) if isinstance(old_val, snbt.String) else None
            if old_s == new_val:
                unchanged_keys.append(k)  # ✅ 找到但一樣
                continue

            dst_root[k] = snbt.String(new_val)
            changed += 1
            changed_keys.append(k)
            continue

        # --- 2) 模板是 List ---
        if isinstance(template_val, SnbtList):
            if isinstance(new_val, list):
                parts = [x for x in new_val if isinstance(x, str)]
            elif isinstance(new_val, str):
                parts = new_val.split("\n")
            else:
                skipped_type_mismatch.append(k)
                continue

            old_py = _list_to_py(old_val)
            if old_py is not None and old_py == parts:
                unchanged_keys.append(k)  # ✅ 找到但一樣
                continue

            dst_root[k] = SnbtList([snbt.String(p) for p in parts])
            changed += 1
            changed_keys.append(k)
            continue

        # 其他型別：不碰（但也算 skipped）
        skipped_type_mismatch.append(k)
        continue

    _write_snbt(dst_path, dst_root)

    if not return_details:
        return changed, candidates

    details = {
        "total_updates": len(updates),
        "missing_in_template": missing_in_template,
        "skipped_type_mismatch": skipped_type_mismatch,
        "unchanged_keys": unchanged_keys,
        "changed_keys": changed_keys,
    }
    return changed, candidates, details

def patch_quest_snbt_file(
    src_path: str,
    dst_path: str,
    updates: Dict[str, Any],
    *,
    return_details: bool = False,
) -> Tuple[int, int] | Tuple[int, int, Dict[str, Any]]:
    """
    專門 patch quest 本體 SNBT：
    - updates 的 key 形式通常是：
        "id:<ID>|title"
        "id:<ID>|subtitle"
        "id:<ID>|description"
      value 可能是 str 或 list[str]（extractor 會把 list join 成 \\n，但保留相容）
    - 遞迴掃整棵 SNBT，找到符合 id 的 Compound 後更新欄位
    """

    src_root = _read_snbt(src_path)
    if not src_root:
        if return_details:
            return (
                0,
                0,
                {
                    "total_updates": len(updates),
                    "changed_keys": [],
                    "unchanged_keys": [],
                    "skipped": [],
                    "missing": [],
                },
            )
        return 0, 0

    dst_root = _read_snbt(dst_path)
    if not dst_root:
        dst_root = src_root

    want: Dict[Tuple[str, str], Any] = {}  # (id, kind) -> val
    skipped: list[str] = []

    for k, v in updates.items():
        if not (
            isinstance(v, str)
            or (isinstance(v, list) and all(isinstance(x, str) for x in v))
        ):
            skipped.append(k)
            continue
        # k = "id:xxxx|title"
        if k.startswith("id:") and "|" in k:
            left, kind = k.split("|", 1)
            qid = left[3:]
            want[(qid, kind)] = v
        else:
            # 沒有 id 的 key（理論上 quest 本體不該用到），先跳過
            skipped.append(k)

    changed = 0
    candidates = 0
    changed_keys: list[str] = []
    unchanged_keys: list[str] = []
    missing: list[str] = []

    def _coerce_to_list(new_val: Any) -> list[str] | None:
        """

    
        """
        if isinstance(new_val, list):
            parts = [x for x in new_val if isinstance(x, str)]
            return parts
        if isinstance(new_val, str):
            return new_val.split("\n")
        return None

    def _apply_field(obj: Compound, kind: str, new_val: Any, tag_key: str):
        """

        回傳：None
        """
        nonlocal changed, candidates

        if kind not in ("title", "subtitle", "description"):
            skipped.append(tag_key)
            return

        old = obj.get(kind)
        if old is None:
            missing.append(tag_key)
            return

        # 模板是 String
        if isinstance(old, snbt.String):
            if not isinstance(new_val, str):
                skipped.append(tag_key)
                return
            candidates += 1
            if str(old) == new_val:
                unchanged_keys.append(tag_key)
                return
            obj[kind] = snbt.String(new_val)
            changed += 1
            changed_keys.append(tag_key)
            return

        # 模板是 List[String]
        if isinstance(old, SnbtList):
            parts = _coerce_to_list(new_val)
            if parts is None:
                skipped.append(tag_key)
                return

            old_py = [str(e) for e in old if isinstance(e, snbt.String)]
            candidates += 1
            if old_py == parts:
                unchanged_keys.append(tag_key)
                return

            obj[kind] = SnbtList([snbt.String(p) for p in parts])
            changed += 1
            changed_keys.append(tag_key)
            return

        skipped.append(tag_key)

    def _recurse(node):
        """

        回傳：None
        """
        if isinstance(node, Compound):
            id_val = node.get("id")
            if isinstance(id_val, snbt.String):
                qid = str(id_val)

                for kind in ("title", "subtitle", "description"):
                    key = (qid, kind)
                    if key in want:
                        _apply_field(node, kind, want[key], f"id:{qid}|{kind}")

            for _, sub in node.items():
                if isinstance(sub, (Compound, SnbtList)):
                    _recurse(sub)

        elif isinstance(node, SnbtList):
            for item in node:
                if isinstance(item, (Compound, SnbtList)):
                    _recurse(item)

    _recurse(dst_root)
    _write_snbt(dst_path, dst_root)

    if not return_details:
        return changed, candidates

    return (
        changed,
        candidates,
        {
            "total_updates": len(updates),
            "changed_keys": changed_keys,
            "unchanged_keys": unchanged_keys,
            "skipped": skipped,
            "missing": missing,
        },
    )

def inject_ftbquests_zh_tw_from_json_old(
    base_config_dir: str,
    zh_tw_lang_json_path: str,
    *,
    template_prefer: str = "zh_cn",
    overwrite_template_copy: bool = True,
) -> dict:
    """
    將 zh_tw JSON 寫回：
      <base_config>/ftbquests/quests/lang/zh_tw/**.snbt

    template_prefer:
      - "zh_cn"：優先用 zh_cn 當模板（最常見）
      - 若 zh_cn 不存在，自動 fallback en_us

    overwrite_template_copy:
      - True：每次都先用模板覆蓋複製一份到 zh_tw（確保結構一致，最穩）
      - False：只在 zh_tw 不存在時才複製模板
    """
    quests_root = os.path.join(base_config_dir, "ftbquests", "quests")
    lang_root = os.path.join(quests_root, "lang")

    prefer_dir = os.path.join(lang_root, template_prefer)
    fallback_dir = os.path.join(lang_root, "en_us")

    template_dir = prefer_dir if os.path.isdir(prefer_dir) else fallback_dir
    if not os.path.isdir(template_dir):
        raise FileNotFoundError(
            f"找不到模板語系資料夾（{prefer_dir} / {fallback_dir} 都不存在）"
        )

    zh_tw_dir = os.path.join(lang_root, "zh_tw")

    # 讀入 zh_tw 扁平 map
    zh_tw_map = _load_json_dict(zh_tw_lang_json_path)
    by_file = split_lang_by_source_file(zh_tw_map)

    # 先確保 zh_tw 目錄存在，並建立模板檔案結構
    if overwrite_template_copy or not os.path.isdir(zh_tw_dir):
        copied = _walk_and_copy_template(template_dir, zh_tw_dir)
    else:
        copied = 0

    # 對每個來源檔寫回
    patched_files = 0
    patched_keys = 0
    candidate_keys = 0
    skipped_default = 0
    missing_template_files = 0

    # _default：沒有 filename| 前綴的 key（通常不該出現，除非你的 make_output_key 規則改過）
    if "_default" in by_file and by_file["_default"]:
        skipped_default = len(by_file["_default"])

    for filename, updates in by_file.items():
        if filename == "_default":
            continue

        # filename 可能是 "abc.snbt" 或 "sub/abc.snbt"（如果你修了 extractor 讓它帶相對路徑）
        src_path = os.path.join(template_dir, filename)
        dst_path = os.path.join(zh_tw_dir, filename)

        if not os.path.isfile(src_path):
            missing_template_files += 1
            continue

        changed, candidates = patch_lang_snbt_file(src_path, dst_path, updates)
        patched_files += 1
        patched_keys += changed
        candidate_keys += candidates

    return {
        "template_used": template_dir,
        "zh_tw_lang_dir": zh_tw_dir,
        "template_files_copied": copied,
        "patched_files": patched_files,
        "patched_keys_changed": patched_keys,
        "patched_keys_candidates": candidate_keys,
        "skipped_default_keys": skipped_default,
        "missing_template_files": missing_template_files,
    }

def inject_ftbquests_quests_from_zh_tw_json(
    *,
    input_config_dir: str,  # 原包 config（只讀）
    output_config_dir: str,  # 翻譯後 config（只寫）
    zh_tw_quests_json_path: str,
) -> dict:
    """
    專門注入 quest 本體：
    - 來源 SNBT：<input_config>/ftbquests/quests/**.snbt
    - 目標 SNBT：<output_config>/ftbquests/quests/**.snbt
    - JSON key 必須是：<relpath>.snbt|id:<ID>|title/subtitle/description
      （因此 extractor 需要改成 rel_path）
    """

    input_config_dir = _normalize_config_dir(input_config_dir)
    output_config_dir = _normalize_config_dir(output_config_dir)

    in_quests = os.path.join(input_config_dir, "ftbquests", "quests")
    out_quests = os.path.join(output_config_dir, "ftbquests", "quests")

    if not os.path.isdir(in_quests):
        raise FileNotFoundError(f"找不到 input quests 目錄：{in_quests}")

    quests_map = _load_json_dict(zh_tw_quests_json_path) or {}
    by_file = split_lang_by_source_file(quests_map)

    copied = walk_and_copy_all_snbt(in_quests, out_quests)

    patched_files = 0
    patched_keys = 0
    candidate_keys = 0
    missing_source_files = 0
    skipped_default = 0

    if "_default" in by_file and by_file["_default"]:
        skipped_default = len(by_file["_default"])

    def _build_filename_index(root_dir: str) -> dict[str, list[str]]:
        """

    
        """
        idx = defaultdict(list)
        for r, _, files in os.walk(root_dir):
            for fn in files:
                if fn.lower().endswith(".snbt"):
                    idx[fn].append(os.path.join(r, fn))
        return dict(idx)

    filename_index = _build_filename_index(in_quests)

    for rel_file, updates in by_file.items():
        if rel_file == "_default":
            continue

        targets: list[tuple[str, str]] = []

        # 1) rel_file 可能是 "chapters/xxx.snbt"（帶路徑）=> 直接用
        if ("/" in rel_file) or ("\\" in rel_file):
            src_path = os.path.join(in_quests, rel_file)
            if not os.path.isfile(src_path):
                missing_source_files += 1
                continue
            dst_path = os.path.join(out_quests, rel_file)
            targets.append((src_path, dst_path))

        # 2) rel_file 只有檔名（你現在的狀況）=> 用索引找 quests/**/同名檔
        else:
            hits = filename_index.get(rel_file, [])
            if not hits:
                missing_source_files += 1
                continue

            # 若同名檔有多個：你可以選擇全部 patch（推薦先全部 patch，至少不漏）
            if len(hits) > 1:
                log_warning(
                    "⚠️ quests 下找到多個同名檔 %s，將全部套用更新（count=%d）",
                    rel_file,
                    len(hits),
                )

            for h in hits:
                rel = os.path.relpath(h, in_quests)
                targets.append((h, os.path.join(out_quests, rel)))

        # 對所有命中的檔案做 patch
        for src_path2, dst_path2 in targets:
            if not os.path.isfile(dst_path2):
                os.makedirs(os.path.dirname(dst_path2), exist_ok=True)
                shutil.copy2(src_path2, dst_path2)

            changed, candidates = patch_quest_snbt_file(src_path2, dst_path2, updates)
            patched_files += 1
            patched_keys += changed
            candidate_keys += candidates

    return {
        "template_quests_dir": in_quests,
        "output_quests_dir": out_quests,
        "template_files_copied": copied,
        "patched_files": patched_files,
        "patched_keys_changed": patched_keys,
        "patched_keys_candidates": candidate_keys,
        "missing_source_files": missing_source_files,
        "skipped_default_keys": skipped_default,
    }

def inject_ftbquests_zh_tw_from_jsons(
    base_config_dir: str,
    zh_tw_lang_json_path: Optional[str],
    zh_tw_quests_json_path: Optional[str],
    *,
    template_prefer: str = "zh_cn",
    overwrite_template_copy: bool = True,
) -> dict:
    """
    注入 zh_tw 語系到 quests/lang 內的 .snbt

    支援兩種結構：
    1) 資料夾制：
       quests/lang/en_us/*.snbt
       quests/lang/zh_cn/*.snbt
    2) 單檔制：
       quests/lang/en_us.snbt
       quests/lang/zh_cn.snbt

    - zh_tw_lang_json_path：可為 None（沒有 lang 要注入）
    - zh_tw_quests_json_path：可為 None（沒有 quests 要注入）
    """

    base_config_dir = _normalize_config_dir(base_config_dir)
    quests_root = os.path.join(base_config_dir, "ftbquests", "quests")
    lang_root = os.path.join(quests_root, "lang")

    # ✅ 至少要有一個來源 JSON
    if not zh_tw_lang_json_path and not zh_tw_quests_json_path:
        raise ValueError("lang_json / quests_json 都是 None，沒有任何可注入的內容")

    # ----------------------------
    # 找模板來源（支援兩種結構）
    # 1) 資料夾制：lang/zh_cn/*.snbt 或 lang/en_us/*.snbt
    # 2) 單檔制：lang/zh_cn.snbt 或 lang/en_us.snbt
    # ----------------------------
    prefer_dir = os.path.join(lang_root, template_prefer)
    fallback_dir = os.path.join(lang_root, "en_us")

    prefer_file = os.path.join(lang_root, f"{template_prefer}.snbt")
    fallback_file = os.path.join(lang_root, "en_us.snbt")

    template_mode: str | None = None  # "dir" | "file"
    template_base: str | None = None  # dir path or file path

    if os.path.isdir(prefer_dir):
        template_mode = "dir"
        template_base = prefer_dir
    elif os.path.isdir(fallback_dir):
        template_mode = "dir"
        template_base = fallback_dir
    elif os.path.isfile(prefer_file):
        template_mode = "file"
        template_base = prefer_file
    elif os.path.isfile(fallback_file):
        template_mode = "file"
        template_base = fallback_file
    else:
        raise FileNotFoundError(
            "找不到模板語系（支援資料夾制與單檔制），嘗試過：\n"
            f"- dir: {prefer_dir}\n"
            f"- dir: {fallback_dir}\n"
            f"- file: {prefer_file}\n"
            f"- file: {fallback_file}\n"
        )

    # ----------------------------
    # ✅ 讀取 JSON（不存在就當空 dict）
    # ----------------------------
    lang_map: dict = {}
    if zh_tw_lang_json_path and os.path.isfile(zh_tw_lang_json_path):
        lang_map = _load_json_dict(zh_tw_lang_json_path) or {}

    quests_map: dict = {}
    if zh_tw_quests_json_path and os.path.isfile(zh_tw_quests_json_path):
        quests_map = _load_json_dict(zh_tw_quests_json_path) or {}

    # ✅ 合併（quests 覆蓋 lang）
    merged_map: dict = {}
    if isinstance(lang_map, dict):
        merged_map.update(lang_map)
    if isinstance(quests_map, dict):
        merged_map.update(quests_map)

    by_file = split_lang_by_source_file(merged_map)

    # ----------------------------
    # ✅ 統計變數：一定要先初始化
    # ----------------------------
    copied = 0
    patched_files = 0
    patched_keys = 0
    candidate_keys = 0
    skipped_default = 0
    missing_template_files = 0

    # ✅ 詳細差異報表（跨所有檔案累積）
    missing_keys_all: list[str] = []
    unchanged_keys_all: list[str] = []
    skipped_keys_all: list[str] = []

    # _default：沒有 filename| 前綴的 key（通常不該出現，除非你的 make_output_key 規則改過）
    if template_mode == "dir":
        if "_default" in by_file and by_file["_default"]:
            skipped_default = len(by_file["_default"])
    else:
        # 單檔制：_default 視為可注入
        skipped_default = 0
    # ----------------------------
    # ✅ 輸出位置：
    # - 資料夾制 -> lang/zh_tw/*.snbt
    # - 單檔制   -> lang/zh_tw.snbt
    # ----------------------------
    zh_tw_dir = os.path.join(lang_root, "zh_tw")  # 資料夾制用
    os.makedirs(lang_root, exist_ok=True)

    if template_mode == "dir":
        # ✅ copy zh_cn -> zh_tw 後，先把整包 SNBT OpenCC(s2twp) 轉繁，再 patch
        template_is_zh_cn = (
            os.path.basename(os.path.normpath(template_base)).lower() == "zh_cn"
        )
    else:
        template_is_zh_cn = os.path.basename(template_base).lower() == "zh_cn.snbt"

    if template_mode == "dir":
        # ---------- 資料夾制 ----------
        os.makedirs(zh_tw_dir, exist_ok=True)

        if overwrite_template_copy or not os.path.isdir(zh_tw_dir):
            copied = _walk_and_copy_template(template_base, zh_tw_dir)
        else:
            copied = 0
        # ✅ 資料夾制：copy zh_cn -> zh_tw 後，先 OpenCC(s2twp) 轉繁，再 patch

        if template_is_zh_cn and copied > 0:
            cfg = load_config()
            rules_path = cfg.get("translator", {}).get(
                "replace_rules_path", "replace_rules.json"
            )
            rules = load_replace_rules(rules_path)

            converted_files = convert_snbt_tree_inplace(zh_tw_dir, rules)
            log_info(
                "🈶 模板 zh_cn 已複製到 zh_tw，並已先 OpenCC(s2twp) 轉換：converted_files=%d",
                converted_files,
            )

        for filename, updates in by_file.items():
            if filename == "_default":
                continue

            src_path = os.path.join(template_base, filename)
            dst_path = os.path.join(zh_tw_dir, filename)

            if not os.path.isfile(src_path):
                missing_template_files += 1
                continue

            # changed, candidates = patch_lang_snbt_file(src_path, dst_path, updates)
            changed, candidates, details = patch_lang_snbt_file(
                src_path, dst_path, updates, return_details=True
            )
            # ✅ A: 摘要直接寫進 message（不用改 log_format 也看得到）
            unchanged_count = len(details.get("unchanged_keys", []))
            skipped_count = len(details.get("skipped_type_mismatch", []))
            missing_count = len(details.get("missing_in_template", []))

            if unchanged_count or skipped_count or missing_count:
                log_info(
                    "ℹ️ [FTB-INJECT] %s | candidates=%d | changed=%d | unchanged=%d | skipped_type=%d | missing=%d",
                    filename,
                    candidates,
                    changed,
                    unchanged_count,
                    skipped_count,
                    missing_count,
                )

            # ✅ B: 詳細 key（只在 DEBUG 才會看到）
            for k in details.get("unchanged_keys", []):
                log_debug("UNCHANGED %s|%s", filename, k)
            for k in details.get("skipped_type_mismatch", []):
                log_debug("SKIPPED(TYPE) %s|%s", filename, k)
            for k in details.get("missing_in_template", []):
                log_debug("MISSING(TEMPLATE) %s|%s", filename, k)

            patched_files += 1
            patched_keys += changed
            candidate_keys += candidates

            # ✅ 加上 file 前綴，之後一眼看懂是哪個檔案的哪個 key
            missing_keys_all += [
                f"{filename}|{k}" for k in details.get("missing_in_template", [])
            ]
            unchanged_keys_all += [
                f"{filename}|{k}" for k in details.get("unchanged_keys", [])
            ]
            skipped_keys_all += [
                f"{filename}|{k}" for k in details.get("skipped_type_mismatch", [])
            ]

        zh_tw_output = zh_tw_dir  # 回傳用（資料夾路徑）

    else:
        # ---------- 單檔制 ----------
        dst_file = os.path.join(lang_root, "zh_tw.snbt")

        if overwrite_template_copy or not os.path.isfile(dst_file):
            import shutil

            shutil.copy2(template_base, dst_file)
            copied = 1
        else:
            copied = 0

        # ✅ 單檔制：copy zh_cn -> zh_tw.snbt 後，先 OpenCC(s2twp) 轉繁，再 patch
        if template_is_zh_cn and copied > 0:
            cfg = load_config()
            rules_path = cfg.get("translator", {}).get(
                "replace_rules_path", "replace_rules.json"
            )
            rules = load_replace_rules(rules_path)

            convert_snbt_file_inplace(dst_file, rules)
            log_info("🈶 模板 zh_cn 單檔已複製到 zh_tw.snbt，並已先 OpenCC(s2twp) 轉換")

        # 單檔制：把所有 updates 合併後 patch 到同一個檔案
        merged_updates: dict = {}

        for filename, updates in by_file.items():
            if isinstance(updates, dict):
                merged_updates.update(updates)

        changed, candidates, details = patch_lang_snbt_file(
            template_base, dst_file, merged_updates, return_details=True
        )
        # ✅ A: 單檔制摘要直接寫進 message（不用改 log_format 也看得到）
        unchanged_count = len(details.get("unchanged_keys", []))
        skipped_count = len(details.get("skipped_type_mismatch", []))
        missing_count = len(details.get("missing_in_template", []))

        if unchanged_count or skipped_count or missing_count:
            log_info(
                "ℹ️ [FTB-INJECT] zh_tw.snbt | candidates=%d | changed=%d | unchanged=%d | skipped_type=%d | missing=%d",
                candidates,
                changed,
                unchanged_count,
                skipped_count,
                missing_count,
            )

        # ✅ B: 單檔制詳細 key（只在 DEBUG 才會看到）
        for k in details.get("unchanged_keys", [])[:50]:
            log_debug("UNCHANGED zh_tw.snbt|%s", k)
        for k in details.get("skipped_type_mismatch", [])[:50]:
            log_debug("SKIPPED(TYPE) zh_tw.snbt|%s", k)
        for k in details.get("missing_in_template", [])[:50]:
            log_debug("MISSING(TEMPLATE) zh_tw.snbt|%s", k)

        missing_keys_all += [f"{k}" for k in details.get("missing_in_template", [])]
        unchanged_keys_all += [f"{k}" for k in details.get("unchanged_keys", [])]
        skipped_keys_all += [f"{k}" for k in details.get("skipped_type_mismatch", [])]

        patched_files = 1
        patched_keys = changed
        candidate_keys = candidates

        zh_tw_output = dst_file  # 回傳用（檔案路徑）

        # ✅ 簡短摘要（不洗版）
    log_info(
        # 1. 訊息主體 (Message)：
        # 這是一段固定字串，方便你在日誌檔案中「搜尋」。
        # 保持固定不變，不要把變數塞進這裡，是為了讓日誌過濾器（Filter）更容易抓取。
        "[INJECT-DIFF] Summary updated",
        # 2. 額外欄位 (Extra Parameter)：
        # 這是 Python logging 模組的強大功能。它會把這些資料封裝成「屬性」。
        extra={
            "missing_in_template": len(missing_keys_all),  # 模板中缺失的鍵數量
            "unchanged": len(unchanged_keys_all),  # 未變動的鍵數量
            "skipped_type_mismatch": len(skipped_keys_all),  # 因類型不符而跳過的數量
        },
    )

    return {
        # ✅ template_dir 已不存在，改回傳 template_base（真實模板來源）
        "template_used": f"{template_base} ({template_mode})",
        # ✅ 這裡回傳「實際產出的位置」：資料夾 or 檔案
        "zh_tw_lang_dir": zh_tw_output,
        "template_files_copied": copied,
        "patched_files": patched_files,
        "patched_keys_changed": patched_keys,
        "patched_keys_candidates": candidate_keys,
        # ✅ 單檔制永遠是 0，資料夾制才有意義
        "skipped_default_keys": skipped_default,
        "missing_template_files": missing_template_files,
        "merged_sources": {
            "lang_json": zh_tw_lang_json_path,
            "quests_json": zh_tw_quests_json_path,
            "lang_keys": len(lang_map) if isinstance(lang_map, dict) else 0,
            "quests_keys": len(quests_map) if isinstance(quests_map, dict) else 0,
            "merged_keys": len(merged_map),
        },
        "diff_report": {
            "missing_in_template": missing_keys_all[:200],
            "unchanged_keys": unchanged_keys_all[:200],
            "skipped_type_mismatch": skipped_keys_all[:200],
            "missing_in_template_count": len(missing_keys_all),
            "unchanged_keys_count": len(unchanged_keys_all),
            "skipped_type_mismatch_count": len(skipped_keys_all),
        },
    }
