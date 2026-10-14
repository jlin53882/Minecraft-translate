"""不靠名單判斷「哪些檔案、哪些字串是玩家看得到的文字」。

判斷只看三件事，不看模組名稱，也不需要資料夾白名單：

1. 語系：路徑片段或檔名尾端的 ``en_us`` / ``zh_tw`` / ``zh_cn``（可帶 ``_`` 前綴、``_0`` 後綴）。
   其他語系（``ja_jp``、``_ru_ru``…）不是來源，也不拿來配對。沒有語系記號的檔案視為原文。
2. 副檔名與內容：文字類副檔名才讀；內容要像一段英文敘述（``is_prose``），
   不是資源 id、路徑、數字、色碼、模板。
3. 結構：JSON 內不碰 ``multiblock`` 這類版面子樹，也不碰 ``type``、``id`` 這類值型別的欄位
   （由值的形狀判斷，不靠欄位名稱清單）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

TEXT_SUFFIXES = (".md", ".mdx", ".txt", ".json", ".lang", ".snbt")

_SOURCE_LANGS = ("en_us", "zh_tw", "zh_cn")
# 語系記號：``zh_tw``／``pt-br``，或常見的單獨語言碼資料夾（``de``、``ru``；兩字母的一般資料夾名如 ``ui`` 不算）
_BARE_LOCALES = "de|fr|es|it|pt|ru|ja|ko|pl|nl|tr|cs|sv|uk"
_LOCALE_CODE = rf"[a-z]{{2}}[_-][a-z]{{2}}|{_BARE_LOCALES}"
_LOCALE_TOKEN = re.compile(rf"^_?({_LOCALE_CODE})(?:_\d+)?$", re.IGNORECASE)
_LOCALE_SUFFIX = re.compile(rf"^(.*?)_({_LOCALE_CODE})(?:_\d+)?$", re.IGNORECASE)

_RESOURCE_ID = re.compile(
    r"^#?[a-z0-9_.\-]+:[a-z0-9_./\-]+(?:[\[{].*)?$", re.IGNORECASE
)
_PATH_LIKE = re.compile(r"^[\w.\-]+(?:/[\w.\-]+)+$")
_COLOR = re.compile(r"^#?[0-9a-fA-F]{3,8}$")
_NUMBER = re.compile(r"^[-+]?\d+(?:\.\d+)?[a-zA-Z]?$")
_TRANSLATION_KEY = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_\-]+){2,}$")
_MARKUP = re.compile(
    r"\$\([^)]*\)|§.|%(?:\d+\$)?[sdf]|\{\d*\}|<[^>]+>|\[[^\]]*\]\([^)]*\)"
)
_WORD = re.compile(r"[A-Za-z]{2,}")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "is",
        "are",
        "be",
        "it",
        "this",
        "that",
        "with",
        "as",
        "at",
        "by",
        "from",
        "can",
        "will",
        "you",
        "your",
        "if",
        "not",
        "when",
        "use",
        "used",
        "which",
        "has",
        "have",
    ]
)


@dataclass(frozen=True)
class LocaleSplit:
    """``lang`` 為來源語系或 ``None``（原文／非來源）；``other`` 表示是其他語系。"""

    lang: str | None
    other: bool
    neutral: str  # 去掉語系記號後的相對路徑


def split_locale(parts: Sequence[str]) -> LocaleSplit:
    """從路徑片段找出語系記號並回傳去掉記號後的中性路徑。"""
    rest = list(parts)
    for idx, part in enumerate(rest):
        stem = part.rsplit(".", 1)[0] if idx == len(rest) - 1 else part
        match = _LOCALE_TOKEN.match(stem)
        if match:
            code = match.group(1).lower().replace("-", "_")
            if idx == len(rest) - 1 and "." in part:
                # 檔名就是語系（lang/en_us.json）：保留副檔名當中性名稱
                neutral = "/".join([*rest[:idx], "*." + part.rsplit(".", 1)[1]])
            else:
                neutral = "/".join([*rest[:idx], *rest[idx + 1 :]])
            return LocaleSplit(
                code if code in _SOURCE_LANGS else None,
                code not in _SOURCE_LANGS,
                neutral,
            )
    last = rest[-1]
    stem, dot, ext = last.rpartition(".")
    match = _LOCALE_SUFFIX.match(stem or last)
    if match:
        code = match.group(2).lower().replace("-", "_")
        base = match.group(1) + (dot + ext if dot else "")
        neutral = "/".join([*rest[:-1], base])
        return LocaleSplit(
            code if code in _SOURCE_LANGS else None,
            code not in _SOURCE_LANGS,
            neutral,
        )
    return LocaleSplit("en_us", False, "/".join(rest))


def is_prose(text: str) -> bool:
    """像一段給人看的英文敘述（非 id／路徑／數字／色碼／翻譯鍵）。"""
    value = text.strip()
    if len(value) < 3 or "\n" not in value and " " not in value:
        # 單字標題（"Overview"）仍可能是標題；交給呼叫端決定是否放行
        return False
    plain = _MARKUP.sub(" ", value)
    if _RESOURCE_ID.match(value) or _PATH_LIKE.match(value) or _COLOR.match(value):
        return False
    if _NUMBER.match(value) or _TRANSLATION_KEY.match(value):
        return False
    words = _WORD.findall(plain)
    if len(words) < 2:
        return False
    letters = sum(len(w) for w in words)
    if letters < 8:
        return False
    return any(w.lower() in _STOPWORDS for w in words) or len(words) >= 4


def is_title_like(text: str) -> bool:
    """短標題（一到三個英文字，首字大寫），用於 ``title``／``name`` 這種位置。"""
    value = text.strip()
    if _RESOURCE_ID.match(value) or _PATH_LIKE.match(value) or _COLOR.match(value):
        return False
    if _NUMBER.match(value) or _TRANSLATION_KEY.match(value):
        return False
    words = _WORD.findall(_MARKUP.sub(" ", value))
    return 1 <= len(words) <= 8 and value[:1].isalpha()


BOM_AND_SPACE = chr(0xFEFF) + " " + chr(9) + chr(13) + chr(10)
_BLANK_LINE = re.compile("\n[ \t]*\n")
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


def markdown_front_matter(text: str) -> tuple[dict[str, str], str]:
    """回傳 (front matter 的簡單 ``key: value`` 與縮排子鍵, 正文)。"""
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and value.strip():
            meta[key.strip()] = value.strip().strip("'\"")
    return meta, text[match.end() :]


# Minecraft 資源包／資料包規格中固定存放非敘述資料的資料夾（規格本身定義，不是各模組的名單）
VANILLA_NON_TEXT_DIRS = frozenset(
    [
        "textures",
        "models",
        "blockstates",
        "shaders",
        "sounds",
        "particles",
        "atlases",
        "font",
        "loot_table",
        "loot_tables",
        "worldgen",
        "recipe",
        "recipes",
        "tags",
        "structure",
        "structures",
        "predicate",
        "predicates",
        "item_modifiers",
        "dimension",
        "dimension_type",
    ]
)

# 給開發者／玩家說明專案本身的文件，不是遊戲內顯示的文字
_PROJECT_DOC_STEM = re.compile(
    r"(?:^|[_\-\d])(?:(?:readme)(?:[_\-].*)?|changelog|license|licence|credits|notice|contributing)$",
    re.IGNORECASE,
)


def is_project_doc_name(path: str) -> bool:
    """README／CHANGELOG 這類說明專案本身的文件，不是遊戲內顯示的文字。"""
    base = path.rsplit("/", 1)[-1]
    return any(
        _PROJECT_DOC_STEM.search(piece) for piece in base.split(".")[:-1] or [base]
    )


_LANG_META_KEYS = frozenset({"language.code", "language.region"})
_COLOR_VALUE = re.compile(r"#?[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?")
_CONST_VALUE = re.compile(r"[A-Z0-9]+(?:_[A-Z0-9]+)+")
_SNAKE_VALUE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)+")
_URL_VALUE = re.compile(r"https?://\S+")


def lang_non_text_reason(key: str, value: str) -> str | None:
    """語言檔條目不是顯示文字時回傳原因：翻譯者備註鍵、語言代碼、色碼、``SEED_OIL_BUCKET``、單獨網址。"""
    text = value.strip()
    if key.startswith("_") or key.lower() in _LANG_META_KEYS:
        return "meta_key"
    if key.lower().endswith((".color", ".hex", "_color")) and _COLOR_VALUE.fullmatch(
        text
    ):
        return "code_value"
    if any(
        pattern.fullmatch(text) for pattern in (_CONST_VALUE, _SNAKE_VALUE, _URL_VALUE)
    ):
        return "code_value"
    return None


def _is_json_text(text: str) -> bool:
    """整份內容能被當成 JSON 讀進來；``[c=#fff]標題`` 這類以括號開頭的敘述文字不算。"""
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def sniff_text_file(
    text: str, suffix: str, name: str = "", *, translated: bool = False
) -> str | None:
    """依內容判斷 ``.md``／``.txt`` 是不是遊戲內顯示的敘述文字；是的話回傳類別。

    ``translated=True`` 時內容是繁中／簡中譯文，敘述文字改看中文字數，不要求英文停用字。
    """
    suffix = suffix.lower()
    if suffix not in (".md", ".mdx", ".txt"):
        return None
    if name:
        parts = name.replace("\\", "/").split("/")
        if is_project_doc_name(name):
            return None
        # 規格資料夾固定在 assets|data/<模組>/ 底下第一層；更深的同名資料夾是手冊自己的
        if len(parts) > 3 and parts[2].lower() in VANILLA_NON_TEXT_DIRS:
            return None
    stripped = text.lstrip(BOM_AND_SPACE)
    if stripped[:1] in ("{", "[") and _is_json_text(
        stripped
    ):  # 副檔名是 .txt 的 JSON 資料
        return None
    if suffix in (".md", ".mdx"):
        meta, body = markdown_front_matter(text)
        if meta.get("title") or any(k in meta for k in ("navigation", "categories")):
            return "guide"
        return "guide" if _prose_paragraphs(body, translated) >= 1 else None
    return "text" if _prose_paragraphs(text, translated) >= 1 else None


_CJK_RUN = re.compile(r"[㐀-鿿]")
_CJK_PROSE_MIN = 8  # 譯文段落至少幾個中文字才算敘述


def _prose_paragraphs(text: str, translated: bool = False) -> int:
    """像敘述文字的段落數；單段需夠長（>= 30 字元）才算，避免單行 id／標題。

    譯文（``translated``）以中文字數判斷：至少 8 個中文字。
    """
    count = 0
    for block in _BLANK_LINE.split(text):
        stripped = block.strip()
        if not stripped or stripped.startswith(("```", "|", "#!")):
            continue
        if translated:
            if len(_CJK_RUN.findall(stripped)) >= _CJK_PROSE_MIN:
                count += 1
        elif is_prose(stripped) and len(stripped) >= 30:
            count += 1
    return count


def iter_json_strings(node: object, path: str = "") -> Iterator[tuple[str, str]]:
    """逐一列出 JSON 內的字串葉節點與其路徑（``a.b[0].c``）。"""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from iter_json_strings(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_json_strings(value, f"{path}[{index}]")


# ---------------------------------------------------------------------------
# 手冊（Patchouli 類）JSON 字串：只看值的形狀，不看欄位名稱
# ---------------------------------------------------------------------------

_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")
_BOOK_MARKUP = re.compile(r"\$\([^)]*\)|§.")
_PLACEHOLDER = re.compile(r"%\d*\$?[sd]|\{\d*\}|\{\{|\}\}")
_LANGKEY = re.compile(r"^[a-z0-9_\-]+(\.[a-z0-9_\-]+){2,}$")
_URL = re.compile(r"^[a-z]+://", re.IGNORECASE)
_PATHLIKE = re.compile(r"^\.?[\w\-.]+(/[\w\-.$]+)+/?$")
_JAVA_CLASS = re.compile(r"^([a-z_]\w*\.)+[A-Z][\w$]*$")
_ANY_RESLOC = re.compile(r"^[&|!~#]*[^\s:]*:[^\s]+$")
_STRICT_RESLOC = re.compile(r"^[&|!~#,]*[a-z0-9_.\-]+:[a-z0-9_./\-#]+$")
_TEMPLATE_VAR = re.compile(r"^#[^\s#]+#$")
_STACK = re.compile(r"^#?[A-Za-z0-9_.\-]+:[A-Za-z0-9_./\-]+\s*[\[{]")
_BOOK_NUMBER = re.compile(r"^[+-]?\d+([.,]\d+)*%?$")
_BOOL = re.compile(r"^(true|false)$", re.IGNORECASE)
_HEX = re.compile(r"^#?[0-9a-fA-F]{3,8}$")
_HEX_UPPER_ONLY = re.compile(r"^[0-9A-F]{6}$")
_LOWER_IDENT = re.compile(r"^#?[a-z0-9_\-./]+$")
_CAMEL = re.compile(r"[a-z][A-Z]")
_HAS_LETTER = re.compile(r"[^\W\d_]")
_WHITESPACE = re.compile("[ \t\r\n]")
_ASCII_LETTER = re.compile(r"[A-Za-z]")


_PLACEHOLDER_TEXT = re.compile(r"^\W*(?:todo|tbd|fixme)\b|lorem\s+ipsum", re.IGNORECASE)


def is_placeholder_text(text: str) -> bool:
    """開發者留下的佔位字（``TODO``、``[TODO: … goes here]``、Lorem ipsum）。"""
    return _PLACEHOLDER_TEXT.search(text) is not None


def classify_book_string(value: str) -> str:
    """手冊 JSON 的字串值是什麼：``"text"``（要翻譯）、``"lang_key"``（指向語言檔的鍵）或 ``"not"``。

    不使用欄位名稱清單：資源 id、旗標、錨點、路徑、網址、數字、色碼、模板變數、
    小寫識別字與駝峰識別字都不是顯示文字。呼叫端負責排除 ``multiblock`` 這類版面子樹。
    """
    s = value.strip()
    if not s or _CJK.search(s):
        return "not"
    if _BOOK_MARKUP.sub("", s).strip() == "":
        return "not"
    if not _ASCII_LETTER.search(_BOOK_MARKUP.sub("", s)):
        return "not"  # 沒有半形英文字母：不是英文原文（已是其他語言，或純符號）
    if _LANGKEY.fullmatch(s.lstrip("# ")):
        return "lang_key"
    if s.startswith(("{", "[{", '["')) and ":" in s:
        return "not"  # NBT／JSON 片段
    if s[:1] == "#" and not _WHITESPACE.search(s):
        return "not"  # 標籤或模板變數（#tier#、#result->iname）
    if _URL.match(s) or _JAVA_CLASS.fullmatch(s) or _STACK.match(s):
        return "not"
    if _BOOK_NUMBER.fullmatch(s) or _BOOL.fullmatch(s):
        return "not"
    if _HEX.fullmatch(s) and (
        s.startswith("#") or re.search(r"\d", s) or _HEX_UPPER_ONLY.fullmatch(s)
    ):
        return "not"
    core = _BOOK_MARKUP.sub("", s)
    if is_placeholder_text(core):
        return "not"
    if not _WHITESPACE.search(s):
        if _ANY_RESLOC.fullmatch(s) or _PATHLIKE.fullmatch(s):
            return "not"
        if _TEMPLATE_VAR.fullmatch(s) or len(s) <= 1:
            return "not"
        if _LOWER_IDENT.fullmatch(core) or _CAMEL.search(core):
            return "not"
        return "text" if _HAS_LETTER.search(s) else "not"
    tokens = _BOOK_MARKUP.sub("", s).split()
    if tokens and all(_STRICT_RESLOC.fullmatch(t) for t in tokens):
        return "not"
    plain = _BOOK_MARKUP.sub("", _PLACEHOLDER.sub("", s))
    return "text" if _HAS_LETTER.search(plain) else "not"


# ---------------------------------------------------------------------------
# 手冊檔案本身的判斷（不需要設定資料夾名稱）
# ---------------------------------------------------------------------------

# Patchouli 規格固定的子資料夾；內容判斷之外的輔助線索，不是各模組的名單
_BOOK_SPEC_DIRS = frozenset({"entries", "categories", "templates"})


def looks_like_book_json(data: object, parts: Sequence[str]) -> bool:
    """依內容結構判斷一個 JSON 是不是手冊的書本／分類／條目檔。

    - 條目：有 ``pages`` 陣列，且裡面是帶 ``type`` 或文字欄位的頁面物件。
    - 書本根目錄的 ``book.json`` 不在此判斷（資源包能否覆寫它尚未確認，所以不處理）。
    - 分類：位於 Patchouli 規格的 ``categories`` 資料夾，且有 ``name``。
    """
    if not isinstance(data, dict):
        return False
    pages = data.get("pages")
    if isinstance(pages, list) and pages:
        pages_ok = [p for p in pages if isinstance(p, dict | str)]
        if pages_ok and any(
            isinstance(p, str)
            or any(k in p for k in ("type", "text", "summary", "title"))
            for p in pages_ok
        ):
            return True
    text = data.get("text")
    if (
        isinstance(text, list)
        and text
        and all(isinstance(t, dict) and "text" in t for t in text)
    ):
        return True  # Tinkers 風格的書頁：text 是 [{"text": ...}]
    if isinstance(data.get("components"), list) and any(
        p.lower() == "templates" for p in parts[:-1]
    ):
        return True  # Patchouli 模板
    in_spec_dir = any(p.lower() in _BOOK_SPEC_DIRS for p in parts[:-1])
    return in_spec_dir and isinstance(data.get("name"), str)


# ---------------------------------------------------------------------------
# 進度（advancement）的顯示文字
# ---------------------------------------------------------------------------


def advancement_texts(data: object) -> dict[str, str]:
    """進度 ``display.title``／``display.description`` 裡的**字面**文字：``{JSON 路徑: 文字}``。

    寫成 ``{"translate": "鍵"}`` 的是語言檔的鍵，由語言檔負責，這裡不收。
    """
    if not isinstance(data, dict) or not isinstance(data.get("display"), dict):
        return {}
    found: dict[str, str] = {}
    for field in ("title", "description"):
        value = data["display"].get(field)
        path = f"display.{field}"
        if isinstance(value, dict) and isinstance(value.get("text"), str):
            value, path = value["text"], f"{path}.text"
        if isinstance(value, str) and classify_book_string(value) == "text":
            found[path] = value
    return found


def looks_like_advancement(data: object) -> bool:
    """有 ``criteria`` 且 ``display`` 帶字面文字的 JSON。"""
    return (
        isinstance(data, dict)
        and isinstance(data.get("criteria"), dict)
        and bool(advancement_texts(data))
    )
