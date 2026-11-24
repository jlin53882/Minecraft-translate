"""Oracle Index（``rearth.oracle``）書籍的路徑契約。

Oracle Index 把模組的遊戲內文件放在 ``assets/oracle_index/books/<書>/`` 底下，頁面是 ``.mdx``，
每個資料夾的選單名稱放在 ``_meta.json``。遊戲讀譯文的方式很單純：

1. 讀 ``<書>/sinytra-wiki.json``，``schema`` 等於 ``"1"`` 用新版（V1）版面，其餘一律舊版（Legacy）。
2. 語系用遊戲目前的語言碼（例如 ``zh_tw``）精確比對，沒有「退回 ``zh_cn``」這回事。
3. 譯文路徑是對頁面路徑做字串替換：``books/<書>`` 換成 ``books/<書>/translated/<語系>``
   （舊版是 ``.translated``）。頁面（``.mdx``）與選單（``_meta.json``）都走同一條規則。

這個模組只回答「一個路徑在 Oracle 書裡扮演什麼角色」與「譯文該放哪裡」，不讀檔案、不翻譯、不寫檔；
掃描、抽取與輸出階段都用它，避免各自猜一次路徑規則。契約依據 Oracle Index 1.4.1，
說明與驗證狀態見 ``docs/developer-guide/oracle-index-books.md``。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

ORACLE_NAMESPACE = "oracle_index"
BOOKS_DIR = "books"
WIKI_META_NAME = "sinytra-wiki.json"
META_NAME = "_meta.json"
PAGE_SUFFIX = ".mdx"
DEFAULT_TARGET_LOCALE = "zh_tw"

# 只有 ``schema`` 恰好是 "1" 才是新版；沒有這個欄位（或檔案不存在）就是舊版
_V1_SCHEMA = "1"
_LOCALE_DIR = re.compile(r"^[a-z]{2,3}_[a-z]{2,4}$")


class Layout(StrEnum):
    """書的資料夾版面。"""

    V1 = "v1"
    LEGACY = "legacy"


class Section(StrEnum):
    """路徑在書裡的位置（譯文與原文各自指向它對應的原文位置）。"""

    CONTENT = "content"  # 內容模式的頁面與選單
    DOCS = "docs"  # 文件模式的頁面與選單（舊版是書根目錄）
    ASSETS = "assets"  # 圖片等素材
    DATA = "data"  # 配方、屬性等資料，不是給人看的文字
    MARKER = "marker"  # sinytra-wiki.json：版面宣告
    OTHER = "other"


@dataclass(frozen=True)
class _LayoutNames:
    content: str
    docs: str | None  # None：書根目錄本身就是文件模式
    translated: str
    assets: str
    data: str


_NAMES: Mapping[Layout, _LayoutNames] = {
    Layout.V1: _LayoutNames("content", "docs", "translated", "assets", "data"),
    Layout.LEGACY: _LayoutNames(".content", None, ".translated", ".assets", ".data"),
}


@dataclass(frozen=True)
class OracleMember:
    """一個 jar／資料夾成員在 Oracle 書裡的身分。"""

    book: str
    layout: Layout
    section: Section
    locale: str | None  # 譯文才有（小寫語系碼）；原文為 None
    source_rel: (
        str  # 相對書根目錄、已去掉 ``translated/<語系>/`` 的路徑（原文與譯文相同）
    )

    @property
    def is_translated(self) -> bool:
        return self.locale is not None

    @property
    def is_page(self) -> bool:
        return self.source_rel.lower().endswith(PAGE_SUFFIX)

    @property
    def is_meta(self) -> bool:
        return self.source_rel.rsplit("/", 1)[-1] == META_NAME

    @property
    def neutral_key(self) -> str:
        """原文與各語系譯文共用的鍵：``<書>/<原文相對路徑>``。"""
        return f"{self.book}/{self.source_rel}"

    @property
    def is_translatable_source(self) -> bool:
        """是否為「要翻譯的英文原文」：內容或文件區的 ``.mdx`` 與 ``_meta.json``。"""
        return (
            not self.is_translated
            and self.section in (Section.CONTENT, Section.DOCS)
            and (self.is_page or self.is_meta)
        )


def layout_from_wiki_meta(raw: str | bytes | None) -> Layout:
    """由 ``sinytra-wiki.json`` 的內容決定版面；檔案不存在或無法解析時為舊版，與遊戲一致。

    ``schema`` 寫成數字 ``1`` 時也視為新版：遊戲以 Gson 讀進字串欄位，數字會轉成 ``"1"``
    （由 Gson 行為推論，尚未用真實 jar 驗證）。
    """
    if raw is None:
        return Layout.LEGACY
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return Layout.LEGACY
    if not isinstance(data, dict):
        return Layout.LEGACY
    schema = data.get("schema")
    if isinstance(schema, bool):
        return Layout.LEGACY
    if isinstance(schema, (str, int)) and str(schema) == _V1_SCHEMA:
        return Layout.V1
    return Layout.LEGACY


def split_book_path(path: str) -> tuple[str, str, tuple[str, ...]] | None:
    """``(書前綴, 書名, 書內路徑片段)``；不是 Oracle 書的檔案回傳 None。

    書前綴是 ``books/<書>/`` 之前、含 ``assets/oracle_index/`` 的部分，保留它才能把譯文放回同一個樹底下
    （成員路徑可能帶翻譯包的外層資料夾）。至少要有一個檔名，只有書根目錄的路徑不算。
    """
    parts = tuple(p for p in path.replace("\\", "/").split("/") if p)
    for index in range(len(parts) - 3):
        if (
            parts[index].lower() == "assets"
            and parts[index + 1] == ORACLE_NAMESPACE
            and parts[index + 2] == BOOKS_DIR
        ):
            prefix = "/".join(parts[: index + 3])
            return prefix, parts[index + 3], parts[index + 4 :] or ()
    return None


def _classify(rest: tuple[str, ...], names: _LayoutNames) -> tuple[Section, str]:
    """書內路徑 → ``(位置, 相對書根目錄的路徑)``；譯文已先去掉語系資料夾。"""
    joined = "/".join(rest)
    head = rest[0]
    if len(rest) == 1 and head == WIKI_META_NAME:
        return Section.MARKER, joined
    if head == names.content:
        return Section.CONTENT, joined
    if head == names.assets:
        return Section.ASSETS, joined
    if head == names.data:
        return Section.DATA, joined
    if names.docs is None:
        # 舊版：書根目錄其餘的檔案都是文件模式（隱藏資料夾已在上面分走）
        if not head.startswith("."):
            return Section.DOCS, joined
    elif head == names.docs:
        return Section.DOCS, joined
    return Section.OTHER, joined


def parse_member(path: str, layout: Layout) -> OracleMember | None:
    """依版面解析成員路徑；不是 Oracle 書的檔案、或只有資料夾時回傳 None。

    ``translated/<語系>/…`` 底下的檔案會回傳語系碼，``section`` 與 ``source_rel`` 指向它對應的原文。
    語系資料夾名稱不像語系碼時，不當成譯文，位置視為 ``OTHER``。
    """
    located = split_book_path(path)
    if located is None:
        return None
    _prefix, book, rest = located
    if not rest:
        return None
    names = _NAMES[layout]
    locale: str | None = None
    if rest[0] == names.translated:
        if len(rest) < 3 or not _LOCALE_DIR.match(rest[1].lower()):
            return OracleMember(book, layout, Section.OTHER, None, "/".join(rest))
        locale = rest[1].lower()
        rest = rest[2:]
    section, source_rel = _classify(rest, names)
    return OracleMember(book, layout, section, locale, source_rel)


def translated_path(
    path: str, layout: Layout, locale: str = DEFAULT_TARGET_LOCALE
) -> str:
    """原文成員路徑對應的譯文路徑（遊戲會去讀的位置）。

    等同遊戲的替換：``books/<書>`` → ``books/<書>/translated/<語系>``（舊版為 ``.translated``）。
    只接受原文路徑；譯文、非 Oracle 書、或不屬於內容／文件區的檔案會丟出 ``ValueError``，
    避免輸出階段把素材或資料檔誤寫成譯文。
    """
    member = parse_member(path, layout)
    if member is None or member.is_translated:
        raise ValueError(f"不是 Oracle 書的原文路徑：{path}")
    if member.section not in (Section.CONTENT, Section.DOCS):
        raise ValueError(f"此位置沒有譯文：{path}")
    if not _LOCALE_DIR.match(locale.lower()):
        raise ValueError(f"不是語系碼：{locale}")
    located = split_book_path(path)
    assert located is not None  # parse_member 已確認
    prefix, book, rest = located
    names = _NAMES[layout]
    return "/".join([prefix, book, names.translated, locale.lower(), *rest])


def infer_layout(path: str) -> Layout:
    """只看路徑推測版面（讀不到 ``sinytra-wiki.json`` 時使用，例如掃描已解壓的資料夾）。

    書根目錄底下第一層是 ``content``、``docs``、``translated``、``assets``、``data`` 之一就是新版，
    其餘（隱藏資料夾或直接放在書根目錄的頁面）是舊版。與遊戲的判斷只在極少數情況不同：
    新版書的根目錄不會有其他檔案，舊版書沒有宣告檔時也不會有這些非隱藏資料夾。
    """
    located = split_book_path(path)
    if located is None or not located[2]:
        return Layout.LEGACY
    v1 = _NAMES[Layout.V1]
    head = located[2][0]
    if head in (v1.content, v1.docs, v1.translated, v1.assets, v1.data):
        return Layout.V1
    return Layout.LEGACY


def _content_member(path: str) -> OracleMember | None:
    member = parse_member(path, infer_layout(path))
    if member is not None and member.section in (Section.CONTENT, Section.DOCS):
        return member
    return None


def is_translatable_page(path: str) -> bool:
    """內容或文件區的 ``.mdx`` 頁面（原文或譯文）。"""
    member = _content_member(path)
    return member is not None and member.is_page


def is_translatable_meta(path: str) -> bool:
    """內容或文件區的 ``_meta.json``（原文或譯文）：選單顯示名稱。"""
    member = _content_member(path)
    return member is not None and member.is_meta


def split_translated(parts: Sequence[str]) -> tuple[str, str] | None:
    """路徑片段含 ``translated/<語系>/``（舊版 ``.translated``）時回傳 ``(語系, 中性路徑)``。

    中性路徑是去掉 ``translated/<語系>`` 之後的路徑，與原文頁相同，用來配對原文與譯文。
    ``translated`` 前面至少要有一層（書的資料夾）；語系資料夾後面還要有檔案。
    """
    for idx in range(1, len(parts) - 2):
        if parts[idx] in ("translated", ".translated") and _LOCALE_DIR.match(
            parts[idx + 1].lower()
        ):
            return parts[idx + 1].lower(), "/".join([*parts[:idx], *parts[idx + 2 :]])
    return None
