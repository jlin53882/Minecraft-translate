"""identity.py

把檔案路徑與 JSON 路徑轉成資料庫的「身分」：``(類型, 模組, 鍵值)``。

同一個項目在不同遊戲版本的 jar / 資料夾中要得到相同的身分，才能跨版本比對：

- lang：``assets/<模組>/lang/en_us.json`` 的頂層鍵值（例如 ``item.foo.bar``）。
- patchouli：``(assets|data)/<模組>/<書籍目錄>/…/<語言>/…/檔案.json`` 內的欄位，
  鍵值為「去掉 assets/data 與語言資料夾的相對路徑 + ``#`` + JSON 路徑」，
  所以書籍放在 ``assets`` 或 ``data`` 底下、不同語言資料夾都不影響身分。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from translation_tool.translation_db.schema import KIND_LANG, KIND_PATCHOULI
from translation_tool.utils.log_unit import log_debug

DEFAULT_PATCHOULI_DIRS: tuple[str, ...] = (
    "patchouli_books",
    "book",
    "manual",
    "guidebook",
)

_LANG_FILE = re.compile(r"^(en_us|zh_tw|zh_cn)\.json$", re.IGNORECASE)
_LANG_DIR = re.compile(r"^_?(en_us|zh_tw|zh_cn)$", re.IGNORECASE)
_ROOT_DIRS = ("assets", "data")


@dataclass(frozen=True)
class FileIdentity:
    """一個語言檔／書籍檔的身分。``lang`` 為 en_us / zh_tw / zh_cn。"""

    kind: str
    mod_id: str
    file_key: str  # lang 為空字串；patchouli 為語言資料夾以外的相對路徑
    lang: str

    def item_key(self, item_path: str) -> str:
        """組出條目鍵值。lang 直接使用 JSON 鍵；patchouli 為 ``檔案鍵#JSON 路徑``。"""
        if self.kind == KIND_LANG:
            return item_path
        return f"{self.file_key}#{item_path}"


def patchouli_dir_names() -> tuple[str, ...]:
    """設定中的書籍目錄名稱（讀不到設定時用預設）。"""
    try:
        from translation_tool.utils.config_manager import load_config

        names = (
            load_config().get("lm_translator", {}).get("patchouli", {}).get("dir_names")
        )
        if names:
            return tuple(str(n) for n in names)
    except Exception as exc:  # noqa: BLE001 - 設定不可用時退回預設，不影響身分計算
        log_debug(f"讀取 patchouli 目錄設定失敗，使用預設：{exc}")
    return DEFAULT_PATCHOULI_DIRS


def classify_parts(
    parts: Sequence[str], dir_names: Sequence[str] | None = None
) -> FileIdentity | None:
    """由「以 assets / data 開頭」的路徑片段判斷身分；不是語言檔或書籍檔時回傳 None。"""
    if len(parts) < 4 or parts[0].lower() not in _ROOT_DIRS:
        return None
    root = parts[0].lower()
    mod_id = parts[1]
    name = parts[-1]

    if root == "assets" and len(parts) == 4 and parts[2] == "lang":
        match = _LANG_FILE.match(name)
        if match:
            return FileIdentity(KIND_LANG, mod_id, "", match.group(1).lower())
        return None

    dirs = tuple(dir_names) if dir_names is not None else patchouli_dir_names()
    if parts[2] not in dirs or not name.lower().endswith(".json"):
        return None
    for idx in range(3, len(parts) - 1):
        match = _LANG_DIR.match(parts[idx])
        if match:
            rel = [*parts[2:idx], *parts[idx + 1 :]]
            return FileIdentity(
                KIND_PATCHOULI, mod_id, "/".join(rel), match.group(1).lower()
            )
    return None


def classify_member(
    member_name: str, dir_names: Sequence[str] | None = None
) -> FileIdentity | None:
    """jar／zip 內成員路徑的身分。

    ``assets``／``data`` 可以出現在路徑中的任何位置（例如翻譯包的 ``pack/1.20/assets/foo/lang/zh_tw.json``）。
    """
    parts = member_name.replace("\\", "/").split("/")
    for idx, part in enumerate(parts):
        if part.lower() in _ROOT_DIRS:
            found = classify_parts(parts[idx:], dir_names)
            if found is not None:
                return found
    return None


def classify_file(
    file: str | Path, root: str | Path, dir_names: Sequence[str] | None = None
) -> FileIdentity | None:
    """已提取到磁碟的檔案身分；路徑以 ``root``（輸入資料夾）為基準。"""
    try:
        rel = Path(file).resolve().relative_to(Path(root).resolve())
    except ValueError:
        rel = Path(file)
    return classify_parts(rel.parts, dir_names)


def split_json_path(path: str) -> list[str]:
    """把 ``a.b[0].c`` 拆成 ``['a', 'b', '[0]', 'c']``（索引獨立成一段）。"""
    return path.replace("[", ".[").split(".") if path else []


def get_by_path(root: object, path: str) -> object | None:
    """與 ``set_by_path`` 相反：依路徑取值；找不到回傳 None。

    頂層鍵值本身含 ``.``（lang 的 ``item.foo.bar``）時，會嘗試把相鄰片段合併成一個鍵。
    """
    segs = split_json_path(path)

    def walk(node: object, i: int) -> object | None:
        if i >= len(segs):
            return node
        seg = segs[i]
        if seg.startswith("[") and seg.endswith("]"):
            if isinstance(node, list) and seg[1:-1].isdigit():
                idx = int(seg[1:-1])
                if 0 <= idx < len(node):
                    return walk(node[idx], i + 1)
            return None
        if not isinstance(node, dict):
            return None
        for j in range(len(segs), i, -1):
            chunk = segs[i:j]
            if any(s.startswith("[") for s in chunk):
                continue
            cand = ".".join(chunk)
            if cand in node:
                found = walk(node[cand], j)
                if found is not None:
                    return found
        return None

    return walk(root, 0)
