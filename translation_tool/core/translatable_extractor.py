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
from translation_tool.core.patchouli_semantics import (
    PatchouliContext,
    patchouli_translatable_items,
)
from translation_tool.core.text_sniffer import (
    VANILLA_NON_TEXT_DIRS,
    advancement_texts,
    is_advancement_document,
    lang_non_text_reason,
    looks_like_book_json,
    value_shape_reason,
)
from translation_tool.utils.config_manager import load_config

_MAX_SNIFF_BYTES = 2 * 1024 * 1024


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


def is_advancement_file(file_path: Path) -> bool:
    """判斷是否為 ``data/<mod>/advancement(s)/*.json`` 檔案。"""
    parts = Path(file_path).parts
    # 輸入路徑可能帶有外層 ``data``／``assets`` 資料夾名稱；由內向外找，
    # 才會以實際資源根目錄判斷，而不是被父目錄同名片段誤判。
    for index in range(len(parts) - 3, -1, -1):
        part = parts[index]
        if part.lower() in {"assets", "data"} and parts[index + 2].lower() in {
            "advancement",
            "advancements",
        }:
            return parts[-1].lower().endswith(".json")
    return False


def find_advancement_json(root: Path) -> list[Path]:
    """找出 Minecraft Advancement JSON，供一般目錄翻譯流程使用。"""
    return [
        path
        for path in root.rglob("*.json")
        if is_advancement_file(path.relative_to(root))
    ]


def is_lang_file(file_path: Path) -> bool:
    """
    判斷是否為標準資源結構中的語言檔（lang 類型）。

    參數：
        file_path: 檔案路徑

    回傳：
        True 是 ``assets/<模組>/lang/<語系>.json``／``.lang`` 或
        ``data/<模組>/lang/<語系>.json``／``.lang``，False 是其他檔案。

    Advancement 優先於路徑片段判斷：即使進度檔的子路徑碰巧叫做
    ``lang``，也不能被當成語言檔而繞過 Advancement 專用 writer。
    """
    path = Path(file_path)
    if is_advancement_file(path):
        return False
    parts = path.parts
    for index, part in enumerate(parts[:-3]):
        if (
            part.lower() in {"assets", "data"}
            and parts[index + 2].lower() == "lang"
            and index + 3 == len(parts) - 1
        ):
            # 現代資源包使用 JSON；scanner 仍支援舊版 Minecraft 的 .lang，
            # 兩者都必須在 canonical lang 路徑內才算語言檔。
            return parts[-1].lower().endswith((".json", ".lang"))
    return False


def _lang_nested_translatable(value: str) -> bool:
    """語言檔巢狀 ``text`` 與陣列裡的字串是否要翻譯；只有符號、數字或佔位字的值不收。"""
    return (
        is_value_translatable(value, is_lang=True) and value_shape_reason(value) is None
    )


def _list_text_translatable(value: str, is_lang: bool, path: str) -> bool:
    """語言檔陣列裡的字串是否要翻譯。"""
    return _lang_nested_translatable(value) if is_lang else False


def _extract_advancement_translatables(json_data, file_path) -> list[dict[str, str]]:
    """依完整 Advancement 文件契約抽取 literal，拒絕 generic JSON fallback。"""
    if not is_advancement_document(json_data):
        return []
    return [
        {
            "file": str(file_path),
            "path": path,
            "text": text,
            "source_text": text,
        }
        for path, text in advancement_texts(json_data).items()
    ]


def _is_advancement_content_file(file_path) -> bool:
    """確認檔案應走 Advancement 專用抽取，而不是語言檔 walker。"""
    return is_advancement_file(Path(file_path))


def extract_translatables(
    json_data,
    file_path,
    *,
    patchouli_context: PatchouliContext | None = None,
):
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
    # 先辨識 Advancement，再判斷 Lang；不能讓父目錄或子路徑中的 ``lang``
    # 片段把合法 Advancement 導向通用 JSON walker。
    is_advancement = is_advancement_file(Path(file_path))
    is_lang = not is_advancement and is_lang_file(Path(file_path))

    if _is_advancement_content_file(file_path):
        # Advancement 必須在抽取、DB scanner 與 writer 共用同一個完整文件契約；
        # 不完整文件安全略過，不退回通用 JSON 抽取器，避免稍後才由 writer 拒絕。
        return _extract_advancement_translatables(json_data, file_path)

    if not is_lang:
        # Patchouli 必須由同一個語意分類器決定 literal/reference/structure。
        # scanner 讀 ZIP 時會傳入 archive context；一般目錄翻譯則由
        # patchouli_semantics 依 book.json/template 建立有界快取。
        return patchouli_translatable_items(
            json_data, file_path, context=patchouli_context
        )

    def walk(obj, base_path=""):
        """遞迴遍歷物件提取可翻譯文字。"""
        if isinstance(obj, dict):
            for k, v in obj.items():
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
