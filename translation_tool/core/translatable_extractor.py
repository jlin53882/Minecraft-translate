"""translation_tool/core/translatable_extractor.py 模組。

用途：從 JSON 檔案中抽取可翻譯的文字內容。
支援 Patchouli 和 Lang 兩種類型的語言檔案。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from pathlib import Path

from translation_tool.core.lm_config_rules import (
    is_translatable_field,
    is_value_translatable,
)
from translation_tool.utils.config_manager import load_config


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
        load_config().get("lm_translator", {}).get("patchouli", {}).get("dir_names", [])
    )
    if dir_names is None:
        dir_names = patchouli_dir_names

    files = []
    for dir_name in dir_names:
        pattern = f"assets/*/{dir_name}/**/*.json"
        files.extend(root.rglob(pattern))

    return files


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
                new_path = f"{base_path}.{k}" if base_path else k

                if is_lang and isinstance(v, str):
                    if base_path == "":
                        if is_value_translatable(v, is_lang=True):
                            items.append(
                                {
                                    "file": str(file_path),
                                    "path": new_path,
                                    "text": v,
                                    "source_text": v,
                                }
                            )
                        continue

                    if k == "text" and is_value_translatable(v, is_lang=True):
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
                    and is_translatable_field(k)
                    and is_value_translatable(v, is_lang=False)
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
                if isinstance(v, str) and is_value_translatable(v, is_lang=is_lang):
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
