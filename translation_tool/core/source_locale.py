"""來源語系的解析、驗證與 Prompt 補充段落（所有 AI 翻譯與智慧校對入口共用）。

設計邊界：

- ``DEFAULT_SOURCE_LOCALES`` 只決定「是否附加語系補充段落」，不是掃描或翻譯的允許名單。
- 語系只接受結構化的 locale token（小寫字母、數字與底線），不做國家／語言名稱白名單。
- 語系來源必須可信：只從掃描時寫入的來源 selector 還原；還原不到就回傳 ``None``（unknown），
  絕不推斷成 ``en_us``。unknown 條目沿用既有 Prompt，不附加任何語系主張。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

DEFAULT_SOURCE_LOCALES = frozenset({"en_us", "zh_tw", "zh_cn"})
TRANSLATION_LOCALES = frozenset({"zh_tw", "zh_cn"})

_LOCALE_TOKEN = re.compile(r"^[a-z]{2,3}(?:_[a-z0-9]{2,8})?$")
_JSON_LANG_SELECTOR = re.compile(r"^lang:([^:\s]+):")
_JS_LANG_SELECTOR = re.compile(r"^ClientEvents\.lang\[([^\]\s]+)\]")

# 補充段落模板：只代入經驗證的 {source_locale}，不得改寫其他部分。
SOURCE_LOCALE_SUPPLEMENT_TEMPLATE = """【本批次來源語系補充指令】

本批次的主要原文語系為 `{source_locale}`，目標語系為 `zh_tw`（台灣繁體中文）。

1. 必須依據 `{source_locale}` 語系理解原文，不得將原文預設為英文。
2. 直接將原文翻譯為符合 Minecraft 遊戲情境的台灣繁體中文。
3. 如果提供相同 key 的 `en_us` 英文參考，應用於確認物品名稱、技術術語、遊戲機制及可能存在的語意歧義。
4. 英文參考只是輔助證據，不應無條件取代本批來源原文。當 `{source_locale}` 與英文內容不同時，必須優先判斷是否存在版本、上下文或本地化差異，不得擅自合併兩者的意思。
5. 沿用既有 Minecraft 官方術語、ModDB 已審核譯文、local context 及格式保護規則。
6. 不得修改 ID、key、佔位符、色碼及其他受保護格式。
7. 若缺少足夠資料來判斷歧義，應保守處理，不得自行補造遊戲設定。

本指令僅補充來源語系與翻譯判斷，不得覆蓋既有 JSON 輸出、資料驗證及翻譯安全規則。"""


class SourceLocaleError(ValueError):
    """批次內混有不同來源語系，或語系 token 不合法。"""


def normalize_source_locale(value: object) -> str | None:
    """把 ``pt-BR``／``PT_br`` 正規化為 ``pt_br``；不合法的 token 回傳 ``None``。"""
    if not isinstance(value, str):
        return None
    token = value.strip().lower().replace("-", "_")
    if not token or len(token) > 16:
        return None
    if not _LOCALE_TOKEN.fullmatch(token):
        return None
    return token


def locale_from_source_selector(selector: str) -> str | None:
    """從 KubeJS 來源 selector 還原語系；無法確定時回傳 ``None``。

    JSON 來源為 ``lang:<locale>:<key>``，JavaScript 來源為 ``ClientEvents.lang[<locale>]...``。
    其他 selector（例如 FTB Quests 的 ``lang:<key>``）沒有語系資訊，回傳 ``None``。
    """
    text = str(selector or "")
    match = _JSON_LANG_SELECTOR.match(text) or _JS_LANG_SELECTOR.match(text)
    if match is None:
        return None
    return normalize_source_locale(match.group(1))


def locale_from_source_path(source_path: str) -> str | None:
    """從語系來源的相對路徑還原 locale。

    FTB Quests 舊版 selector 只有 ``lang:<key>``，無法攜帶 locale；
    這類來源仍可由 ``lang/<locale>/...`` 或 ``lang/<locale>.snbt``
    的檔案契約安全還原。只檢查 ``lang`` 目錄下的下一段，避免把任意
    檔名誤當成語系。
    """
    parts = [part for part in re.split(r"[/\\]+", str(source_path or "")) if part]
    for index, part in enumerate(parts[:-1]):
        if part.casefold() != "lang":
            continue
        candidate = parts[index + 1]
        stem = candidate.rsplit(".", 1)[0] if "." in candidate else candidate
        locale = normalize_source_locale(stem)
        if locale is not None:
            return locale
    return None


def resolve_source_locale(selectors: Iterable[str]) -> str | None:
    """依同一條目的所有來源 selector 決定來源語系。

    翻譯語系（``zh_tw``／``zh_cn``）的來源只代表譯文，不影響原文語系而略過。
    任何非翻譯來源若無法還原語系，或還原出多個不同語系，都回傳 ``None``（unknown），
    不做多數決，以免錯誤來源被當成可信語系。
    """
    source_locales: set[str] = set()
    for selector in selectors:
        locale = locale_from_source_selector(selector)
        if locale is None:
            return None
        if locale in TRANSLATION_LOCALES:
            continue
        source_locales.add(locale)
    if len(source_locales) != 1:
        return None
    return next(iter(source_locales))


# 沒有語系概念的種類（KubeJS 腳本字串、FTB 任務結構）：來源紀錄不會帶語系，原文依慣例是英文。
# 語系未知時以 en_us 比對已審核參考；已知是其他語系的參考仍然不相容。
LOCALELESS_KINDS = frozenset({"kubejs_script", "ftb_quests"})


def effective_source_locale(kind: str | None, locale: object) -> str | None:
    """已審核參考比對用的項目語系：未知且種類沒有語系概念時視為 ``en_us``，其餘維持未知。"""
    normalized = normalize_source_locale(locale)
    if normalized is None and kind in LOCALELESS_KINDS:
        return "en_us"
    return normalized


def reviewed_locale_compatible(
    item_locale: object, reference_locale: str | None
) -> bool:
    """已審核參考（整句重用、術語表）是否可用於這個來源語系的待翻項目。

    ``reference_locale`` 是資料庫查出的參考語系：已知語系 token、``unknown:<id>``
    （有來源紀錄卻無法確認語系），或空字串（升級前的舊條目，沒有任何來源紀錄，內容是 jar 的英文）。

    - 語系未知的參考永遠不相容，不得自動視為相容。
    - 舊條目（沒有來源紀錄）只相容於 ``en_us`` 或語系未知的項目（升級前的重用行為）。
    - 已知語系的參考只相容於語系相同的項目；項目語系未知時不相容。
    """
    item = normalize_source_locale(item_locale)
    reference = reference_locale or ""
    if reference.startswith("unknown:"):
        return False
    if reference == "":
        return item is None or item == "en_us"
    return item is not None and item == reference


def is_default_source_locale(locale: str | None) -> bool:
    """預設語系（或 unknown）不附加補充段落。"""
    return locale is None or locale in DEFAULT_SOURCE_LOCALES


def batch_source_locale(items: Sequence[Mapping[str, object]]) -> str | None:
    """回傳批次的非預設來源語系；全部預設或 unknown 時回傳 ``None``。

    批次規劃以來源語系作為分組邊界，因此正常情況下不會混入不同語系；
    若真的混入，直接拋出錯誤（fail closed），不得把其中一種語系套用到全部項目。
    """
    locales: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        raw = item.get("source_locale")
        if raw in (None, ""):
            continue
        locale = normalize_source_locale(raw)
        if locale is None:
            raise SourceLocaleError(f"不合法的來源語系：{raw!r}")
        locales.add(locale)
    if len(locales) > 1:
        raise SourceLocaleError(f"同一批次混有多種來源語系：{sorted(locales)}")
    if not locales:
        return None
    locale = next(iter(locales))
    return None if is_default_source_locale(locale) else locale


def source_locale_supplement(locale: str) -> str:
    """回傳補充段落；語系必須已通過 :func:`normalize_source_locale`。"""
    normalized = normalize_source_locale(locale)
    if normalized is None:
        raise SourceLocaleError(f"不合法的來源語系：{locale!r}")
    return SOURCE_LOCALE_SUPPLEMENT_TEMPLATE.format(source_locale=normalized)
