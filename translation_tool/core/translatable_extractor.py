"""translation_tool/core/translatable_extractor.py 模組。

用途：從 JSON 檔案中抽取可翻譯的文字內容。
支援 Patchouli 和 Lang 兩種類型的語言檔案。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import json
from pathlib import Path

from translation_tool.core.book_dirs import DEFAULT_BOOK_DIRS, find_book_dir_index
from translation_tool.core.lm_config_rules import (
    is_value_translatable,
)
from translation_tool.core.text_sniffer import (
    VANILLA_NON_TEXT_DIRS,
    book_field,
    classify_book_string,
    lang_non_text_reason,
    looks_like_book_json,
    value_shape_reason,
)
from translation_tool.utils.config_manager import load_config

# Patchouli 結構性欄位：整個子樹只描述版面或方塊圖樣，沒有顯示文字。
# 例如 ``multiblock.pattern`` 的列 ``" 0 "``，翻譯或削掉空白都會讓每列長度不一致，
# 整本書載入失敗。
_MAX_SNIFF_BYTES = 2 * 1024 * 1024
NON_TEXT_PATCHOULI_KEYS = frozenset({"multiblock"})


def find_patchouli_json(root: Path, dir_names=None):
    """
    找出指定根目錄下的所有 Patchouli JSON 檔案。

    參數：
        root: 專案根目錄路徑
        dir_names: 自訂目錄名稱列表，預設從設定檔讀取

    回傳：
        Path 物件列表
    """
    patchouli_dir_names = (
        load_config()
        .get("lm_translator", {})
        .get("patchouli", {})
        .get("dir_names", list(DEFAULT_BOOK_DIRS))
    )
    detect_by_content = dir_names is None
    if dir_names is None:
        dir_names = patchouli_dir_names

    files = []
    seen: set[Path] = set()
    for path in root.rglob("*.json"):
        parts = path.relative_to(root).parts
        if (
            len(parts) >= 4
            and parts[0] == "assets"
            and find_book_dir_index(parts, dir_names) is not None
            and path not in seen
        ):
            seen.add(path)
            files.append(path)
            continue
        if detect_by_content and _is_book_json_by_content(path, parts):
            seen.add(path)
            files.append(path)

    return files


def _is_book_json_by_content(path: Path, parts: tuple[str, ...]) -> bool:
    """資料夾名稱沒對上時，依內容判斷是不是手冊檔（提取階段放的 ``<模組>_extracted/`` 前綴會先去掉）。"""
    if parts and parts[0].endswith("_extracted"):
        parts = parts[1:]
    if len(parts) < 4 or parts[0] not in ("assets", "data"):
        return False
    if parts[2].lower() in VANILLA_NON_TEXT_DIRS or parts[-1].lower() == "book.json":
        return False
    if len(parts) == 4 and parts[2] == "lang":
        return False
    try:
        if path.stat().st_size > _MAX_SNIFF_BYTES:
            return False
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return False
    return looks_like_book_json(data, parts[2:])


def find_lang_json(root: Path):
    """
    找出指定根目錄下的所有 Lang JSON 檔案。

    參數：
        root: 專案根目錄路徑

    回傳：
        Path 物件列表
    """
    return list(root.rglob("assets/*/lang/*.json"))


def is_lang_file(file_path: Path) -> bool:
    """
    判斷是否為語言檔（lang 類型）。

    參數：
        file_path: 檔案路徑

    回傳：
        True 是 lang 檔，False 是 Patchouli 檔
    """
    return "lang" in file_path.parts


def _lang_nested_translatable(value: str) -> bool:
    """語言檔巢狀 ``text`` 與陣列裡的字串是否要翻譯；只有符號、數字或佔位字的值不收。"""
    return (
        is_value_translatable(value, is_lang=True) and value_shape_reason(value) is None
    )


def _list_text_translatable(value: str, is_lang: bool, path: str) -> bool:
    """陣列裡的字串是否要翻譯：lang 檔用既有規則，手冊檔依上層欄位與值的形狀。"""
    if is_lang:
        return _lang_nested_translatable(value)
    return classify_book_string(value, book_field(path)) == "text"


def extract_translatables(json_data, file_path):
    """
    從 JSON 資料中抽取可翻譯的項目。

    根據檔案類型（lang 或 patchouli）使用不同的判斷邏輯：
    - Lang 檔：根據路徑和欄位名稱判斷
    - Patchouli 檔：根據欄位名稱和值判斷

    參數：
        json_data: JSON 資料（dict 或 list）
        file_path: 檔案路徑

    回傳：
        可翻譯項目列表，每項包含 file、path、text、source_text
    """
    items = []
    is_lang = is_lang_file(Path(file_path))

    def walk(obj, base_path=""):
        """遞迴遍歷物件提取可翻譯文字。"""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if not is_lang and k in NON_TEXT_PATCHOULI_KEYS:
                    continue
                new_path = f"{base_path}.{k}" if base_path else k

                if is_lang and isinstance(v, str):
                    if base_path == "":
                        if (
                            is_value_translatable(v, is_lang=True)
                            and lang_non_text_reason(k, v) is None
                            and value_shape_reason(v) is None
                        ):
                            items.append(
                                {
                                    "file": str(file_path),
                                    "path": new_path,
                                    "text": v,
                                    "source_text": v,
                                }
                            )
                        continue

                    if k == "text" and _lang_nested_translatable(v):
                        items.append(
                            {
                                "file": str(file_path),
                                "path": new_path,
                                "text": v,
                                "source_text": v,
                            }
                        )
                    continue

                elif (
                    not is_lang
                    and isinstance(k, str)
                    and isinstance(v, str)
                    and classify_book_string(v, k) == "text"
                ):
                    items.append(
                        {
                            "file": str(file_path),
                            "path": new_path,
                            "text": v,
                            "source_text": v,
                        }
                    )
                else:
                    walk(v, new_path)

        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                new_path = f"{base_path}[{i}]"
                if isinstance(v, str) and _list_text_translatable(v, is_lang, new_path):
                    items.append(
                        {
                            "file": str(file_path),
                            "path": new_path,
                            "text": v,
                            "source_text": v,
                        }
                    )
                else:
                    walk(v, new_path)

    walk(json_data)
    return items
