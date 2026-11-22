"""Mod 資料庫機翻的同原文合併：相同內容只送 AI 一次，譯文寫回同組所有條目。

同一組＝送一個 AI 請求就能代表全部成員：

- 內容模組識別相同（:func:`content_identity.content_mod_value`：KubeJS／FTB 語系不分模組包版本，
  其他類型就是 ``mod_id``），與跨版本沿用對「同一個模組」的定義一致；
- 類型相同、原文完全相同（大小寫與空白都要一樣）；
- 來源語系的批次身分相同：預設語系與無法確認的語系視為同一組，其他語系各自一組，
  與 :func:`moddb_translate_service.build_items` 的批次分組規則一致（同一組會拿到相同的 prompt）。

每組依查詢順序的第一筆是代表條目，只有代表送 AI；代表的譯文通過品質檢查後，同組條目以
相同譯文一起寫回資料庫。代表被品質檢查拒絕或回填原文時，整組都不寫入。翻譯快取只記錄
實際送 AI 的代表條目。單次上限計算的是組數（送 AI 的筆數），不是條目數。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from translation_tool.core.source_locale import is_default_source_locale
from translation_tool.translation_db.content_identity import content_mod_value

# 資料庫未翻譯條目：(id, 類型, 模組, 鍵值, 原文)
EntryRow = tuple[int, str, str, str, str]


@dataclass(frozen=True)
class MergedSelection:
    """合併後要送 AI 的代表條目，以及每個代表對應的同組條目。"""

    rows: tuple[EntryRow, ...] = ()
    twins: Mapping[int, tuple[EntryRow, ...]] = field(default_factory=dict)

    @property
    def ai_count(self) -> int:
        """實際送 AI 的筆數（組數）。"""
        return len(self.rows)

    @property
    def entry_count(self) -> int:
        """這次涵蓋的條目數（代表＋同組條目）。"""
        return self.ai_count + sum(len(rows) for rows in self.twins.values())

    @property
    def merged_count(self) -> int:
        """因相同原文而不必另外送 AI 的條目數。"""
        return self.entry_count - self.ai_count

    def twins_of(self, entry_id: object) -> tuple[EntryRow, ...]:
        """代表條目的同組條目；不是代表或沒有同組時回傳空 tuple。"""
        return self.twins.get(entry_id, ()) if isinstance(entry_id, int) else ()


def merge_key(row: EntryRow, locale: str | None) -> tuple[str, str, str, str]:
    """條目的同原文分組鍵：(類型, 內容模組識別, 來源語系批次身分, 原文)。"""
    _entry_id, kind, mod_id, _key, en_us = row
    locale_part = "" if is_default_source_locale(locale) else str(locale)
    return (str(kind), content_mod_value(kind, mod_id), locale_part, en_us)


def select_merged_rows(
    rows: Iterable[EntryRow],
    source_locales: Mapping[int, str | None],
    limit: int = 0,
    *,
    check: Callable[[], None] | None = None,
) -> MergedSelection:
    """依查詢順序分組；``limit`` > 0 時只保留前 ``limit`` 組（同組條目不受上限影響）。

    原文只有空白的條目不納入（沒有可送 AI 的內容）。
    """
    groups: dict[tuple[str, str, str, str], list[EntryRow]] = {}
    for row in rows:
        if check is not None:
            check()
        if not str(row[4] or "").strip():
            # 只有空白的原文沒有可翻譯內容；資料庫只擋空字串，這裡擋空白字串，
            # 否則引擎會把整批濾空而中止任務。
            continue
        key = merge_key(row, source_locales.get(int(row[0])))
        members = groups.get(key)
        if members is not None:
            members.append(row)
        elif not limit or len(groups) < limit:
            groups[key] = [row]
    return MergedSelection(
        rows=tuple(members[0] for members in groups.values()),
        twins={
            int(members[0][0]): tuple(members[1:])
            for members in groups.values()
            if len(members) > 1
        },
    )
