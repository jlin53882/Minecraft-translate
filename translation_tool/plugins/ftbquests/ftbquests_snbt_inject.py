"""translation_tool/plugins/ftbquests/ftbquests_snbt_inject.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# ftbquests_snbt_inject.py
# FTB Quests SNBT 注入工具：將已整理好的 zh_tw JSON 寫回 quests/lang/zh_tw/*.snbt

from __future__ import annotations

import os
import re
import shutil
from collections import defaultdict
from typing import Any

import ftb_snbt_lib as snbt
import orjson
from ftb_snbt_lib.tag import Compound  # 避免跟 typing.List 混淆
from ftb_snbt_lib.tag import List as SnbtList

from .ftbquests_snbt_extractor import (
    merge_translated_lines,
    walk_snbt_file_tolerant,
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
    """載入 JSON 檔案為字典。"""
    if not os.path.isfile(path):
        return {}
    with open(path, "rb") as f:
        return orjson.loads(f.read())


def split_lang_by_source_file(lang_map: dict) -> dict[str, dict[str, str]]:
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
    out: dict[str, dict[str, str]] = {}
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
    """讀取 SNBT 檔案；結尾多餘的 ``}`` 與掃描端一樣在記憶體忽略，其餘失敗回傳 None。

    回寫會以 ``snbt.dumps`` 重寫整個輸出檔，所以容錯讀取後輸出的檔案括號是平衡的。
    """
    data, _dropped = walk_snbt_file_tolerant(path)
    return data


def _write_snbt(path: str, root: Compound) -> None:
    """寫入 SNBT 檔案。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(snbt.dumps(root))


def _load_existing_target_or_template(dst_path: str, template: Compound) -> Compound:
    """讀取既有目標；存在但損壞時拒絕以英文模板覆蓋。"""
    dst_root = _read_snbt(dst_path)
    if os.path.exists(dst_path) and dst_root is None:
        raise ValueError(f"既有目標 SNBT 無法解析，拒絕以英文模板覆蓋：{dst_path}")
    return template if dst_root is None else dst_root


def patch_lang_snbt_file(
    src_path: str,
    dst_path: str,
    updates: dict[str, Any],
    *,
    return_details: bool = False,  # ✅ 新增：要不要回傳詳細資訊
) -> tuple[int, int] | tuple[int, int, dict[str, Any]]:
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
    if src_root is None:
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

    dst_root = _load_existing_target_or_template(dst_path, src_root)

    changed = 0
    candidates = 0

    # ✅ 詳細統計
    missing_in_template: list[str] = []
    skipped_type_mismatch: list[str] = []
    unchanged_keys: list[str] = []
    changed_keys: list[str] = []

    def _list_to_py(v):
        """將 SnbtList 物件轉換為標準 Python 列表，並將內部元素格式化為字串。"""
        if isinstance(v, SnbtList):
            out = []
            for e in v:
                out.append(str(e))
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

            # 空行、{image:...}、{@pagebreak} 等沒送去翻譯的行要留在原位
            parts = merge_translated_lines(list(template_val), parts)
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
    updates: dict[str, Any],
    *,
    return_details: bool = False,
) -> tuple[int, int] | tuple[int, int, dict[str, Any]]:
    """
    專門 patch quest 本體 SNBT：
    - updates 的 key 形式支援：
        "id:<ID>|title"
        "id:<ID>|subtitle"
        "id:<ID>|description"
        "id:<ID>|description[<slot>]"
      value 可能是 str 或 list[str]。帶 slot 的 key 只會更新同一個原始
      description ordinal；不帶 slot 的舊格式仍保留相容性。
    - 遞迴掃整棵 SNBT；同一檔案若有重複 ID，拒絕猜測回寫目標。
    """

    src_root = _read_snbt(src_path)
    if src_root is None:
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

    dst_root = _load_existing_target_or_template(dst_path, src_root)

    # 新版 extractor 會把 description 的每個 slot 寫成
    # ``id:<ID>|description[<index>]``；舊版 ``id:<ID>|description``
    # 仍保留相容性，供既有輸出檔使用。
    want: dict[tuple[str, str, int | None], Any] = {}
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
            left, field = k.split("|", 1)
            qid = left[3:]
            match = re.fullmatch(r"(title|subtitle|description)(?:\[(\d+)\])?", field)
            if match is None:
                skipped.append(k)
                continue
            kind = match.group(1)
            index = int(match.group(2)) if match.group(2) is not None else None
            want[(qid, kind, index)] = v
        else:
            # 沒有 id 的 key（理論上 quest 本體不該用到），先跳過
            skipped.append(k)

    want_by_quest: dict[str, list[tuple[str, int | None, Any]]] = defaultdict(list)
    for (qid, kind, index), value in want.items():
        want_by_quest[qid].append((kind, index, value))

    changed = 0
    candidates = 0
    changed_keys: list[str] = []
    unchanged_keys: list[str] = []
    missing: list[str] = []

    id_occurrences: defaultdict[str, int] = defaultdict(int)

    def _count_ids(node):
        if isinstance(node, Compound):
            id_val = node.get("id")
            if isinstance(id_val, snbt.String):
                id_occurrences[str(id_val)] += 1
            for sub in node.values():
                if isinstance(sub, (Compound, SnbtList)):
                    _count_ids(sub)
        elif isinstance(node, SnbtList):
            for item in node:
                if isinstance(item, (Compound, SnbtList)):
                    _count_ids(item)

    _count_ids(dst_root)

    def _coerce_to_list(new_val: Any) -> list[str] | None:
        """將輸入值強制轉換為字串列表；若為列表則篩選出字串，若為字串則依換行符分割。"""
        if isinstance(new_val, list):
            parts = [x for x in new_val if isinstance(x, str)]
            return parts
        if isinstance(new_val, str):
            return new_val.split("\n")
        return None

    def _apply_field(
        obj: Compound,
        kind: str,
        slot_index: int | None,
        new_val: Any,
        tag_key: str,
    ):
        """應用翻譯到欄位。"""
        nonlocal changed, candidates

        if kind not in ("title", "subtitle", "description"):
            skipped.append(tag_key)
            return

        field_value = obj.get(kind)
        if field_value is None:
            missing.append(tag_key)
            return

        # 新版 slot-level key 必須對應原始 List 的同一個 ordinal；
        # 不接受把單筆譯文猜測套到另一個 slot。
        if slot_index is not None:
            if not isinstance(field_value, SnbtList) or not (
                0 <= slot_index < len(field_value)
            ):
                missing.append(tag_key)
                return
            old_slot = field_value[slot_index]
            if not isinstance(old_slot, snbt.String) or not isinstance(new_val, str):
                skipped.append(tag_key)
                return
            candidates += 1
            if str(old_slot) == new_val:
                unchanged_keys.append(tag_key)
                return
            field_value[slot_index] = snbt.String(new_val)
            changed += 1
            changed_keys.append(tag_key)
            return

        old = field_value
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

            parts = merge_translated_lines(list(old), parts)
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
        """遞迴應用翻譯到節點。"""
        if isinstance(node, Compound):
            id_val = node.get("id")
            if isinstance(id_val, snbt.String):
                qid = str(id_val)

                if qid in want_by_quest:
                    if id_occurrences[qid] != 1:
                        skipped.extend(
                            (
                                f"id:{qid}|{kind}"
                                if slot_index is None
                                else f"id:{qid}|{kind}[{slot_index}]"
                            )
                            for kind, slot_index, _new_val in want_by_quest[qid]
                        )
                    else:
                        for kind, slot_index, new_val in want_by_quest[qid]:
                            label = (
                                f"id:{qid}|{kind}"
                                if slot_index is None
                                else f"id:{qid}|{kind}[{slot_index}]"
                            )
                            _apply_field(node, kind, slot_index, new_val, label)

            for sub in node.values():
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
