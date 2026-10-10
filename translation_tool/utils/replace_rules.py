"""Replacement-rule ownership, compilation, and cache implementation."""

import heapq
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

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
        # Regex patterns do not have literal-prefix keywords; a fixed-rule miss
        # must not suppress regex rules that can still match the input.
        if self.regex_rules:
            return True
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
