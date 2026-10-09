"""translation_tool/utils/text_processor.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import heapq
import os
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import orjson
from opencc import OpenCC

from .config_manager import resolve_project_path
from .log_unit import log_error, log_info, log_warning


def _resolve_rules_path(path: str):
    """將相對規則路徑解析為專案內的完整絕對路徑。"""
    return resolve_project_path(path)


_thread_local = threading.local()
_CJK_PATTERN = re.compile(r"([\u4e00-\u9fff]+)")


def get_converter():
    """獲取當前執行緒專用的 OpenCC 實例"""
    if not hasattr(_thread_local, "converter"):
        _thread_local.converter = OpenCC("s2twp")
    return _thread_local.converter


# =========================
# replace rules 快取（執行緒安全）
# =========================
_RULES_CACHE_LOCK = threading.Lock()
# id(規則清單) → (規則清單本身, 內容簽章, 編譯結果)；保留清單參考避免 id 被重用
_RULES_CACHE: dict[int, tuple[Sequence, Any, "_CompiledRules"]] = {}
_RULES_CACHE_MAX = 4


class _TrackedRule(dict):
    """會在被修改時通知所屬 ReplaceRules 的規則 dict（其餘行為與 dict 相同）。"""

    __slots__ = ("_owner",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._owner = None

    def _bump(self):
        if self._owner is not None:
            self._owner._bump()

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._bump()

    def __delitem__(self, key):
        super().__delitem__(key)
        self._bump()

    def __ior__(self, other):
        result = super().__ior__(other)
        self._bump()
        return result

    def clear(self):
        super().clear()
        self._bump()

    def pop(self, *args):
        result = super().pop(*args)
        self._bump()
        return result

    def popitem(self):
        result = super().popitem()
        self._bump()
        return result

    def setdefault(self, key, default=None):
        result = super().setdefault(key, default)
        self._bump()
        return result

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self._bump()


class ReplaceRules(list):
    """load_replace_rules 回傳的規則清單：任何修改（含規則 dict 就地修改）都會遞增 revision。

    apply_replace_rules 以 (清單, revision) 判斷編譯快取是否仍有效，O(1)，
    3 萬條規則逐段文字套用時不必每次都比對整份規則內容。
    """

    def __init__(self, iterable=()):
        super().__init__(self._adopt(r) for r in iterable)
        self.revision = 0

    def _adopt(self, rule):
        # 已屬於其他 ReplaceRules 的規則要複製一份：同一個物件只能通知一個 owner，
        # 共用會讓另一個 owner 的編譯快取在規則被修改後過期。
        if isinstance(rule, _TrackedRule):
            if rule._owner is not None and rule._owner is not self:
                rule = _TrackedRule(rule)
        elif isinstance(rule, dict):
            rule = _TrackedRule(rule)
        if isinstance(rule, _TrackedRule):
            rule._owner = self
        return rule

    def _bump(self):
        self.revision += 1

    def __setitem__(self, index, value):
        if isinstance(index, slice):
            value = [self._adopt(v) for v in value]
        else:
            value = self._adopt(value)
        super().__setitem__(index, value)
        self._bump()

    def __delitem__(self, index):
        super().__delitem__(index)
        self._bump()

    def __iadd__(self, other):
        self.extend(other)
        return self

    def __imul__(self, n):
        result = super().__imul__(n)
        self._bump()
        return result

    def append(self, rule):
        super().append(self._adopt(rule))
        self._bump()

    def extend(self, rules):
        super().extend(self._adopt(r) for r in rules)
        self._bump()

    def insert(self, index, rule):
        super().insert(index, self._adopt(rule))
        self._bump()

    def pop(self, *args):
        result = super().pop(*args)
        self._bump()
        return result

    def remove(self, rule):
        super().remove(rule)
        self._bump()

    def clear(self):
        super().clear()
        self._bump()

    def sort(self, *args, **kwargs):
        super().sort(*args, **kwargs)
        self._bump()

    def reverse(self):
        super().reverse()
        self._bump()

    def snapshot(self):
        """Freeze list membership while retaining O(1) invalidation tracking."""
        return _ReplaceRulesSnapshot(tuple(self), self)


@dataclass(frozen=True, slots=True)
class _ReplaceRulesSnapshot(Sequence[dict[str, str]]):
    """Read-only shallow snapshot that follows edits to its source rule rows."""

    _items: tuple[dict[str, str], ...]
    _source: ReplaceRules

    def __getitem__(self, index):
        return self._items[index]

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    @property
    def revision(self):
        return self._source.revision


def _rules_signature(rules: Sequence[dict[str, str]]):
    """編譯快取的內容簽章。

    ReplaceRules：revision（O(1)）。其他清單：逐條 (from, to)，
    正確但為 O(規則數)，大量規則請使用 load_replace_rules 回傳的清單。
    """
    if isinstance(rules, (ReplaceRules, _ReplaceRulesSnapshot)):
        return ("revision", rules.revision)
    return (
        "content",
        tuple(
            (rule.get("from"), rule.get("to")) if isinstance(rule, dict) else None
            for rule in rules
        ),
    )


class _CompiledRules:
    """預先整理好的替換規則（依規則清單建立一次，所有執行緒共用、唯讀）。

    固定字串規則維持原本「依序（長詞優先）逐條 replace」的語意，
    但只檢查「前兩個字（單字規則為該字）出現在目前文字中」的規則：
    - by_prefix：規則前綴 → 規則索引（遞增）
    - 套用一條規則後，重新計算文字中新出現的前綴並把對應（索引較後）的規則加入候選，
      因此串接替換（dst 內含其他規則的 src）結果與逐條檢查完全相同。
    """

    def __init__(self, rules: Sequence[dict[str, str]]):
        literal_rules: list[tuple[str, str]] = []
        regex_rules: list[tuple[re.Pattern, str]] = []
        keywords: set[str] = set()

        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if "from" not in rule or "to" not in rule:
                continue

            src = rule["from"]
            dst = rule["to"]

            looks_like_regex = any(ch in src for ch in ".?*[]()\\")
            if looks_like_regex:
                try:
                    dst_fixed = re.sub(r"\\\\(\d+)", r"\\\1", dst)
                    dst_fixed = re.sub(r"\$(\d+)", r"\\\1", dst_fixed)
                    pattern = re.compile(src)
                    regex_rules.append((pattern, dst_fixed))
                except re.error:
                    literal_rules.append((src, dst))
                    if src:
                        keywords.add(src[:2])
            else:
                literal_rules.append((src, dst))
                if src:
                    keywords.add(src[:2])

        literal_rules.sort(key=lambda x: len(x[0]), reverse=True)

        self.literal_rules = literal_rules
        self.regex_rules = regex_rules
        # 與舊版相同的「是否可能命中」預檢：規則前兩字（去空白後）出現在去空白的文字中
        self.spaceless_keywords = {k.replace(" ", "") for k in keywords}
        self.keyword_lengths = sorted({len(k) for k in self.spaceless_keywords})
        by_prefix: dict[str, list[int]] = {}
        for idx, (src, _dst) in enumerate(literal_rules):
            if src:
                by_prefix.setdefault(src[:2], []).append(idx)
        self.by_prefix = by_prefix

    def may_hit(self, text: str) -> bool:
        """等同舊版逐條 `k in text or k 去空白 in text 去空白` 的預檢（O(文字長度)）。"""
        if not self.spaceless_keywords:
            return True
        if "" in self.spaceless_keywords:
            return True
        compact = text.replace(" ", "")
        keys = self.spaceless_keywords
        for n in self.keyword_lengths:
            for i in range(len(compact) - n + 1):
                if compact[i : i + n] in keys:
                    return True
        return False

    @staticmethod
    def _grams(text: str) -> set[str]:
        """文字中所有單字與相鄰兩字（規則前綴只可能是其中之一）。"""
        grams = set(text)
        grams.update(text[i : i + 2] for i in range(len(text) - 1))
        return grams

    def apply_literals(self, text: str) -> str:
        literal_rules = self.literal_rules
        by_prefix = self.by_prefix
        seen = self._grams(text)
        heap: list[int] = []
        queued: set[int] = set()
        for gram in seen:
            for idx in by_prefix.get(gram, ()):
                queued.add(idx)
                heap.append(idx)
        heapq.heapify(heap)
        while heap:
            idx = heapq.heappop(heap)
            src, dst = literal_rules[idx]
            if src not in text:
                continue
            text = text.replace(src, dst)
            # 取代後可能出現新的前綴（dst 內部或與前後文相接處）→ 之後的規則也列入候選
            for gram in self._grams(text) - seen:
                seen.add(gram)
                for later in by_prefix.get(gram, ()):
                    if later > idx and later not in queued:
                        queued.add(later)
                        heapq.heappush(heap, later)
        return text


def _get_compiled_rules(rules: Sequence[dict[str, str]]) -> _CompiledRules:
    """依規則清單取得（或建立）編譯好的規則。

    以清單物件與內容簽章判斷：新清單、增刪、以及就地修改 from / to 都會重建
    （舊版每個執行緒只建立一次；之後改成 id + 長度，仍漏掉同長度的就地修改）。
    不同執行緒使用不同規則清單時各自取得對應結果，互不污染。
    """
    signature = _rules_signature(rules)
    entry = _RULES_CACHE.get(id(rules))
    if entry is not None and entry[0] is rules and entry[1] == signature:
        return entry[2]
    compiled = _CompiledRules(rules)
    with _RULES_CACHE_LOCK:
        _RULES_CACHE.pop(id(rules), None)
        if len(_RULES_CACHE) >= _RULES_CACHE_MAX:
            _RULES_CACHE.pop(next(iter(_RULES_CACHE)))
        _RULES_CACHE[id(rules)] = (rules, signature, compiled)
    return compiled


def apply_replace_rules(text: str, rules: Sequence[dict[str, str]]) -> str:
    """應用替換規則到給定的文字。

    語意與舊版相同（固定字串依長詞優先逐條套用、可串接；正則最後套用；
    文字不含任何規則前兩字時整段略過），但只檢查可能命中的規則：
    3 萬條規則時不再對每段文字掃過全部規則。
    """

    if not isinstance(text, str):
        return text

    compiled = _get_compiled_rules(rules)

    # ---------- 快路徑 1：極短字串 ----------
    if len(text) < 2:
        return text

    # ---------- 快路徑 2：不可能命中 ----------
    if not compiled.may_hit(text):
        return text

    text = compiled.apply_literals(text)

    for pattern, repl in compiled.regex_rules:
        text = pattern.sub(repl, text)

    return text


# --- 檔案讀寫與文字處理工具函式 ---
def load_replace_rules(path: str) -> list[dict[str, str]]:
    """
    從指定的 JSON 檔案載入替換規則（orjson 版），並自動進行安全排序：
    - 固定字串規則：from 長度由長到短（長詞優先）
    - 正則規則：保持原順序
    """
    resolved_path = _resolve_rules_path(path)
    if not resolved_path.exists():
        log_warning("找不到替換規則檔案: %s，將略過替換處理。", resolved_path)
        return []

    try:
        with resolved_path.open("rb") as f:
            rules = orjson.loads(f.read())
    except Exception as e:  # noqa: BLE001
        log_error("讀取替換規則檔案 %s 失敗: %s", resolved_path, e)
        return []

    if not isinstance(rules, list):
        log_error("替換規則檔案格式錯誤（需為 list）: %s", resolved_path)
        return []

    fixed_rules: list[dict[str, str]] = []
    regex_rules: list[dict[str, str]] = []

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if "from" not in rule or "to" not in rule:
            continue

        src = rule["from"]
        looks_like_regex = any(ch in src for ch in ".?*[]()\\")
        if looks_like_regex:
            regex_rules.append(rule)
        else:
            fixed_rules.append(rule)

    fixed_rules.sort(key=lambda r: len(r["from"]), reverse=True)
    # ReplaceRules：規則被修改時會遞增 revision，讓 apply_replace_rules 的編譯快取失效
    sorted_rules = ReplaceRules(fixed_rules + regex_rules)

    log_info(
        "載入替換規則完成：固定字串 %d 條（已長詞優先排序），正則 %d 條",
        len(fixed_rules),
        len(regex_rules),
    )
    return sorted_rules


def save_replace_rules(
    path: str, rules: list[dict[str, str]], *, raise_on_error: bool = False
):
    """將替換規則儲存到指定的 JSON 檔案（orjson 版）。

    ``raise_on_error`` keeps the historical log-and-continue behavior for
    best-effort callers while allowing durable user actions to observe failure.
    """
    resolved_path = _resolve_rules_path(path)
    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        with resolved_path.open("wb") as f:
            f.write(
                orjson.dumps(
                    rules, option=orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE
                )
            )
    except Exception as e:
        log_error("儲存替換規則到 %s 失敗: %s", resolved_path, e)
        if raise_on_error:
            raise


def load_custom_translations(folder_path: str, filename="table.tsv") -> dict[str, str]:
    """從指定資料夾載入自訂的翻譯表 (TSV 格式)。"""
    custom_map = {}
    file_path = resolve_project_path(folder_path) / filename
    if not file_path.exists():
        log_info(f"自訂翻譯檔 {file_path} 不存在，略過。")
        return custom_map
    try:
        import pandas as pd

        df = pd.read_csv(
            file_path, sep="\t", header=None, names=["source", "translation"]
        )
        for _, row in df.iterrows():
            if pd.notna(row["source"]) and pd.notna(row["translation"]):
                custom_map[str(row["source"])] = str(row["translation"])
        log_info(f"成功從 {file_path} 載入 {len(custom_map)} 條自訂翻譯。")
    except Exception as e:  # noqa: BLE001
        log_error(f"讀取自訂翻譯檔 {file_path} 失敗: {e!r}")
    return custom_map


def safe_convert_text(text: str) -> str:
    """安全的文字轉換，處理空值與例外。"""
    if not text:
        return text
    conv = get_converter()
    return _CJK_PATTERN.sub(lambda m: conv.convert(m.group(1)), text)


def convert_text(text: str, rules: list[dict[str, str]] | None = None) -> str:
    """
    統一的「純文字」處理入口：
    - 安全簡轉繁（CJK-only s2twp）
    - 套用 replace rules（如果有）
    用途：.snbt / .md / .js / 任何純文字
    """
    if not isinstance(text, str) or not text:
        return text

    out = safe_convert_text(text)
    if rules:
        out = apply_replace_rules(out, rules)
    return out


def convert_snbt_file_inplace(
    path: str, rules: list[dict[str, str]] | None = None
) -> bool:
    """
    就地轉換單一 .snbt（或任何純文字檔）內容。
    回傳：是否有變更。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        dst = convert_text(src, rules)
        if dst != src:
            with open(path, "w", encoding="utf-8") as f:
                f.write(dst)
            return True
        return False
    except Exception as e:  # noqa: BLE001
        log_error("convert_snbt_file_inplace 失敗: %s (%s)", path, e)
        return False


def convert_snbt_tree_inplace(
    root_dir: str, rules: list[dict[str, str]] | None = None
) -> int:
    """
    遞迴掃描資料夾，把所有 .snbt 就地轉繁（CJK-only + rules）。
    回傳：有變更的檔案數。
    用途：inject copy zh_cn -> zh_tw 後，先整包轉繁再 patch
    """
    changed = 0
    for r, _, files in os.walk(root_dir):
        for fn in files:
            if fn.lower().endswith(".snbt"):
                fp = os.path.join(r, fn)
                if convert_snbt_file_inplace(fp, rules):
                    changed += 1
    return changed


def recursive_translate_dict(data: Any, rules: Sequence[dict[str, str]]) -> Any:
    """
    (僅用於簡轉繁) 遞迴地對一個字典或列表中的所有字串值進行 OpenCC 轉換和規則替換。
    """
    if isinstance(data, dict):
        return {k: recursive_translate_dict(v, rules) for k, v in data.items()}
    if isinstance(data, list):
        return [recursive_translate_dict(item, rules) for item in data]
    if isinstance(data, str):
        return apply_replace_rules(safe_convert_text(data), rules)
    return data


def recursive_translate(
    data: Any, rules: Sequence[dict[str, str]], custom_translations: dict[str, str]
) -> Any:
    """
    修改點：
    1. 移除 converter 參數 (不需要再從外部傳入)
    2. 遞迴呼叫時也移除 converter
    3. 字串翻譯改用 safe_convert_text
    """
    if isinstance(data, dict):
        new_dict = {}
        for key, value in data.items():
            # 優先檢查自訂翻譯
            if isinstance(value, str) and value in custom_translations:
                new_dict[key] = custom_translations[value]
            else:
                # ✅ 修改：遞迴時不再傳遞 converter
                new_dict[key] = recursive_translate(value, rules, custom_translations)
        return new_dict

    elif isinstance(data, list):
        # ✅ 修改：遞迴時不再傳遞 converter
        return [recursive_translate(item, rules, custom_translations) for item in data]

    elif isinstance(data, str):
        # ✅ 修改：使用 safe_convert_text 取代 converter.convert
        # 這會自動處理執行緒安全，並確保「内存」變「記憶體」
        translated_text = safe_convert_text(data)
        return apply_replace_rules(translated_text, rules)

    else:
        return data


def orjson_dump_file(obj, fp, *, indent2: bool = True, newline: bool = True):
    """
    用 orjson 寫入檔案物件 fp。
    - indent2=True: 等同 json.dump(..., indent=2)
    - orjson 預設就是 UTF-8 且不會把中文變成 \\uXXXX（等同 ensure_ascii=False）
    """
    option = 0
    if indent2:
        option |= orjson.OPT_INDENT_2
    if newline:
        option |= orjson.OPT_APPEND_NEWLINE

    data = orjson.dumps(obj, option=option)
    fp.write(data)


def orjson_pretty_str(obj) -> str:
    """
    orjson_pretty_str 的 Docstring

    :param obj: 說明
    :return: 說明
    :rtype: str
    """

    return orjson.dumps(
        obj, option=orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE
    ).decode("utf-8")
