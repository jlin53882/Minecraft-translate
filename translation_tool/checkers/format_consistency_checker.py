"""translation_tool/checkers/format_consistency_checker.py 模組。

用途：逐檔比對 en_us 與 zh_tw 語系資料夾，找出譯文與原文的特殊字元（色碼、``%s``、
``{0}`` 等）不一致的條目。檢查規則與 Mod DB 的品質檢查共用
（``translation_issues``），沒有第二份規則。
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import orjson as json

from translation_tool.translation_db.quality import format_mismatches
from translation_tool.translation_db.schema import KIND_LANG


def _read_json_dict(path: str) -> dict[str, Any]:
    with open(path, "rb") as f:
        data = json.loads(f.read())
    if not isinstance(data, dict):
        raise TypeError(f"語系檔必須是 JSON 物件，實際為 {type(data).__name__}")
    return data


def find_format_mismatches(
    en_data: dict[str, Any], tw_data: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """兩份語系資料中，共同的 key 裡譯文與原文特殊字元不一致的條目。

    只比對雙方都是非空字串的值；沒有譯文或型別不同的條目不在此檢查範圍（缺漏由 Key 缺失檢查處理）。
    回傳 ``{key: {"en_us": 原文, "zh_tw": 譯文, "issues": [差異說明]}}``。
    """
    return {
        key: {"en_us": en_data[key], "zh_tw": tw_data[key], "issues": issues}
        for key, issues in format_mismatches(en_data, tw_data, kind=KIND_LANG).items()
    }


def check_format_consistency_generator(
    en_us_dir: str, zh_tw_dir: str, output_dir: str
) -> Generator[dict[str, Any], None, None]:
    """比對 en_us 與 zh_tw 資料夾，把格式不一致的條目寫成報告（保留相對路徑）。"""
    os.makedirs(output_dir, exist_ok=True)
    yield {"progress": 0.0, "log": "開始檢查譯文與原文的特殊字元是否一致..."}

    en_files = [
        os.path.join(root, name)
        for root, _, files in os.walk(en_us_dir)
        for name in files
        if name.endswith(".json")
    ]
    if not en_files:
        yield {
            "progress": 1.0,
            "log": f"在 '{en_us_dir}' 中未找到任何 en_us.json 檔案。",
            "error": True,
        }
        return

    total_files = len(en_files)
    checked_keys = 0
    mismatched_keys = 0
    files_with_mismatch = 0
    files_without_tw = 0

    for index, en_path in enumerate(en_files, 1):
        progress = index / total_files
        rel_path = os.path.relpath(en_path, en_us_dir)
        tw_path = os.path.join(zh_tw_dir, rel_path)
        if not os.path.exists(tw_path):
            files_without_tw += 1
            yield {
                "progress": progress,
                "log": f"[略過] 找不到對應的繁中檔案：{rel_path}（缺漏請用 Key 缺失檢查）。",
            }
            continue
        try:
            en_data = _read_json_dict(en_path)
            tw_data = _read_json_dict(tw_path)
            found = find_format_mismatches(en_data, tw_data)
        except Exception as exc:  # noqa: BLE001 - 單檔失敗只回報，不中斷整批
            yield {
                "progress": progress,
                "log": f"處理 {rel_path} 時出錯: {exc}",
                "error": True,
            }
            continue
        checked_keys += len(set(en_data) & set(tw_data))
        if not found:
            yield {"progress": progress, "log": f"檢查 {rel_path} ... OK。"}
            continue
        files_with_mismatch += 1
        mismatched_keys += len(found)
        output_path = os.path.join(output_dir, rel_path)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(json.dumps(found, option=json.OPT_INDENT_2))
        yield {
            "progress": progress,
            "log": f"在 {rel_path} 中找到 {len(found)} 個特殊字元不一致的條目。",
        }

    yield {"progress": 1.0, "log": "--- 格式一致性檢查完成 ---"}
    yield {
        "progress": 1.0,
        "log": (
            f"共比對 {checked_keys} 個共同的 key，在 {files_with_mismatch} 個檔案中找到 "
            f"{mismatched_keys} 個特殊字元不一致的條目"
            + (
                f"；{files_without_tw} 個檔案沒有對應的繁中檔"
                if files_without_tw
                else ""
            )
            + "。"
        ),
    }
    yield {"progress": 1.0, "log": f"報告已儲存至: {output_dir}"}
