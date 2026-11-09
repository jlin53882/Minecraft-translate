"""智慧校對的本地 context 與官方術語 evidence 快照。

每筆候選會附上可重播且有界的證據：同 key 的已審核譯文、同模組的已審核
glossary 術語、同文的跨模組參考、模組自帶的未審核 zh_tw，以及內建的官方
Minecraft 在地化術語。證據只來自本地資料庫與內建官方語系，不查詢網路。
"""

from __future__ import annotations

import heapq
import re
import sqlite3
from itertools import islice
from typing import Any

from translation_tool.core.official_locale import (
    load_official_locale_index,
    normalize_terminology_text,
    terminology_term_allowed,
    terminology_terms_for_item,
)
from translation_tool.core.source_locale import (
    effective_source_locale,
    reviewed_locale_compatible,
)
from translation_tool.core.terminology_model import (
    evidence_context_entry,
    from_official_match,
)
from translation_tool.translation_db.content_identity import (
    content_key_sql,
    content_key_value,
    content_mod_sql,
    content_mod_value,
)
from translation_tool.translation_db.quality import quality_state
from translation_tool.translation_db.schema import (
    KIND_FTB_QUEST_LANG,
    KIND_FTB_QUESTS,
    KIND_KUBEJS_LANG,
    KIND_KUBEJS_SCRIPT,
    KIND_LANG,
    SRC_JAR_TW,
)
from translation_tool.translation_db.sql_helpers import reference_locale_sql
from translation_tool.utils.text_matching import (
    build_term_prefix_index,
    contains_term,
    matching_terms,
    term_canonical_key,
)

# 第 11 欄（索引 10）是參考的來源語系（見 ``reference_locale_sql``）：證據必須與候選的來源語系相容。
_REFERENCE_LOCALE_INDEX = 10
_REVIEWED_ENTRY_COLUMNS = (
    "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
    "f.zh_tw, f.source, f.review_status, f.revision, "
    f"{reference_locale_sql('e')} "
    "FROM entry e JOIN translation f ON f.entry_id=e.id "
)
_LANGUAGE_KINDS = (KIND_LANG, KIND_KUBEJS_LANG, KIND_FTB_QUEST_LANG)
_TARGET_ROW_LIMIT = 96  # 每個 kind、每個版本分組的詞組命中列上限


def _scoped_phrase_sql(phrase_count: int, scope_sql: str) -> str:
    """模組包範圍（KubeJS／FTB）的詞組命中查詢。

    範圍條件是 EXISTS 子查詢，沒有索引提示時計畫器會掃整個 kind 或版本，再逐列探測
    ``entry_origin``（每次約 16 ms，且每筆候選都要查）。``INDEXED BY idx_entry_en``
    讓詞組先走 en_us 索引，再用範圍與審核狀態過濾（每次不到 1 ms）。
    不含 ORDER BY／LIMIT：排序與上限由呼叫端在記憶體完成，避免臨時排序。
    """
    placeholders = ",".join("?" * phrase_count)
    return (
        _REVIEWED_ENTRY_COLUMNS.replace(
            "FROM entry e JOIN", "FROM entry e INDEXED BY idx_entry_en JOIN", 1
        )
        + "WHERE e.en_us IN ("
        + placeholders
        + ") AND e.kind=? AND "
        + scope_sql
        + " AND f.review_status='reviewed' AND trim(f.zh_tw)<>''"
    )


class _LocalContextBuilder:
    """為單一候選建立 local context；跨候選共用的查詢結果存在 ``cache``。"""

    def __init__(
        self,
        db: Any,
        cache: dict[str, Any],
        *,
        entry_id: int,
        kind: str,
        mc_version: str,
        mod_id: str,
        key: str,
        en_us: str,
    ) -> None:
        self.db = db
        self.cache = cache
        self.entry_id = entry_id
        self.kind = kind
        self.mc_version = mc_version
        self.mod_id = mod_id
        self.key = key
        self.en_us = en_us
        self.key_rows_cache = cache.setdefault("key_rows", {})
        self.quality_cache = cache.setdefault("quality", {})

    @property
    def item_locale(self) -> str | None:
        """候選自己的來源語系；沒有來源紀錄或無法確認時為 ``None``（未知，不視為任何語系）。"""
        if "item_locale" not in self.cache:
            self.cache["item_locale"] = {}
        known = self.cache["item_locale"]
        if self.entry_id not in known:
            row = self.db._one(
                f"SELECT {reference_locale_sql('e')} FROM entry e WHERE e.id=?",
                (self.entry_id,),
            )
            known[self.entry_id] = effective_source_locale(
                self.kind, row[0] if row else None
            )
        return known[self.entry_id]

    def _locale_ok(self, row: tuple) -> bool:
        """證據列的來源語系與候選相容（未知語系的參考永遠不相容）。"""
        return reviewed_locale_compatible(
            self.item_locale, row[_REFERENCE_LOCALE_INDEX]
        )

    def _glossary_scope(self) -> str | None:
        """從 scanner 建立的 mod id 取得 KubeJS／FTB 的模組包身分。"""
        scoped_kinds = {
            KIND_KUBEJS_LANG,
            KIND_KUBEJS_SCRIPT,
            KIND_FTB_QUEST_LANG,
            KIND_FTB_QUESTS,
        }
        if self.kind not in scoped_kinds or "@" not in self.mod_id:
            return None
        _prefix, scope = self.mod_id.split("@", 1)
        return scope or None

    def _glossary_kinds(self) -> tuple[str, ...]:
        """限制外掛語系 glossary 只取同一模組包的同格式語系。"""
        if (
            self.kind in {KIND_KUBEJS_LANG, KIND_KUBEJS_SCRIPT}
            and self._glossary_scope()
        ):
            return (KIND_KUBEJS_LANG,)
        if (
            self.kind in {KIND_FTB_QUEST_LANG, KIND_FTB_QUESTS}
            and self._glossary_scope()
        ):
            return (KIND_FTB_QUEST_LANG,)
        return _LANGUAGE_KINDS

    def _glossary_scope_sql(self) -> tuple[str, tuple[Any, ...]]:
        """回傳同包範圍條件；沒有可信 scope 時退回舊 mod_id 契約。"""
        scope = self._glossary_scope()
        if scope is not None:
            return (
                (
                    "EXISTS (SELECT 1 FROM entry_origin eo "
                    "WHERE eo.entry_id=e.id AND eo.pack_scope=?)"
                ),
                (scope,),
            )
        return "e.mod_id=?", (self.mod_id,)

    def _glossary_cache_scope(self) -> tuple[str, str, str | None, tuple[str, ...]]:
        return (self.kind, self.mod_id, self._glossary_scope(), self._glossary_kinds())

    def build(self) -> list[dict[str, Any]]:
        """依證據優先順序組合並去重後的 context。"""
        context = self._same_key_context(self._same_key_rows())
        glossary_rows = [
            row for row in self._glossary_rows_for_item() if self._locale_ok(row)
        ]
        official = load_official_locale_index()
        # 官方 Minecraft 術語是英文；只用於英文或語系未知的候選。
        official_context = (
            self._official_context(official)
            if self.item_locale in (None, "en_us")
            else []
        )
        glossary_context = self._glossary_context(glossary_rows, official)
        context.extend(glossary_context)
        context.extend(self._lang_name_fallback_context(official, glossary_context))
        context.extend(self._same_text_context())
        module_locale_context = self._module_locale_context()
        # 同一資料庫列可能同時由 same-key 與 glossary 路徑取得；保留第一個較具體的路徑。
        unique: dict[str, dict[str, Any]] = {}
        for item in (*official_context, *module_locale_context, *context):
            unique.setdefault(str(item["id"]), item)
        return list(unique.values())[:48]

    def _quality_details(self, row: tuple) -> tuple[str, tuple[str, ...], str]:
        """取得 reviewed row 的格式品質狀態與可顯示診斷。"""
        quality_key = (int(row[0]), row[9], row[6], row[1])
        if quality_key not in self.quality_cache:
            self.quality_cache[quality_key] = quality_state(
                str(row[5]), str(row[6]), kind=str(row[1])
            )
        return self.quality_cache[quality_key]

    def _cached_rows(
        self, cache_name: str, sql: str, params: tuple[Any, ...]
    ) -> list[tuple]:
        """讀取或建立指定查詢的快取資料列。"""
        bucket = self.cache.setdefault(cache_name, {})
        if params not in bucket:
            bucket[params] = self.db._q(sql, params)
        return bucket[params]

    def _language_rows(
        self,
        cache_name: str,
        where_sql: str,
        params: tuple[Any, ...],
        *,
        order_key,
        limit: int | None = None,
        kinds: tuple[str, ...] = _LANGUAGE_KINDS,
    ) -> list[tuple]:
        """分別查詢語系 kind，再合併已排序的 index stream。

        ``kind IN (...)`` 會讓 SQLite 為跨 kind 的 ORDER BY 建立暫存樹；
        每個 kind 都有相同的索引前綴，因此分兩條有序 stream 合併可保留
        `smart_review` 的 no-temp-sort 契約，也讓未來新增語系 kind 時只需
        擴充 `_LANGUAGE_KINDS`。
        """
        streams = [
            self._cached_rows(
                cache_name,
                _REVIEWED_ENTRY_COLUMNS + "WHERE e.kind=? AND " + where_sql,
                (kind, *params),
            )
            for kind in kinds
        ]
        merged = heapq.merge(*streams, key=order_key)
        if limit is None:
            return list(merged)
        return list(islice(merged, max(0, limit)))

    def _db_context_item(
        self,
        row: tuple,
        *,
        reference_type: str,
        match_type: str,
        confidence: str,
        limitations: str,
        en_us_value: str | None = None,
    ) -> dict[str, Any]:
        """建立含來源、修訂版與品質限制的資料庫證據項目。"""
        status, issues, whitespace = self._quality_details(row)
        detail_parts = [limitations]
        if status != "consistent":
            detail_parts.append(f"reviewed quality={status}")
            if issues:
                detail_parts.append("格式問題：" + "、".join(issues))
            if whitespace:
                detail_parts.append(whitespace)
        return {
            "id": f"db:{row[0]}:source:{row[7]}:revision:{row[9]}",
            "legacy_id": f"db:{row[0]}",
            "entry_id": int(row[0]),
            "reference_type": reference_type,
            "mod_id": str(row[3]),
            "kind": str(row[1]),
            "key": str(row[4]),
            "mc_version": str(row[2]),
            "en_us": str(row[5]) if en_us_value is None else en_us_value,
            "zh_tw": str(row[6]),
            "review_status": str(row[8] or "reviewed"),
            "source": row[7],
            "source_label": self.db.source_catalog.label(row[7]),
            "match_type": match_type,
            "confidence": confidence if status == "consistent" else "low",
            "quality_status": status,
            "limitations": "; ".join(detail_parts),
        }

    # -- 同 key 的已審核譯文 -------------------------------------------------

    def _same_key_rows(self) -> list[tuple]:
        """取得同 key 的已審核列（不含目前條目），品質合格者優先。"""
        cache_key = (self.kind, self.mod_id, self.key, self.mc_version, self.en_us)
        # 同一 key 可能跨版本或原文改版；cache identity 必須涵蓋實際 context
        # 範圍，避免把另一版本的 reviewed evidence 套到目前候選。
        if cache_key not in self.key_rows_cache:
            self.key_rows_cache[cache_key] = self._query_same_key_rows()
        return [
            row
            for row in self.key_rows_cache[cache_key]
            if int(row[0]) != self.entry_id and self._locale_ok(row)
        ]

    def _query_same_key_rows(self) -> list[tuple]:
        """分層抓取同 key 的已審核列，再把品質合格的資料排到名額前面。"""
        mc_version, en_us = self.mc_version, self.en_us
        base_sql = (
            _REVIEWED_ENTRY_COLUMNS
            + f"WHERE e.kind=? AND {content_mod_sql('e')}=? AND {content_key_sql('e')}=? "
            "AND f.review_status='reviewed' AND trim(f.zh_tw)<>'' "
        )
        # 先分層抓取，再在本地把品質合格的資料排到 LIMIT 前面；
        # 不讓 24 筆舊版本或格式不合格資料吃掉目前版本的參考名額。
        tier_queries = (
            (
                "AND e.mc_version=? AND e.en_us=? ORDER BY e.id LIMIT 96",
                (mc_version, en_us),
            ),
            (
                "AND e.mc_version=? AND e.en_us<>? ORDER BY e.id LIMIT 96",
                (mc_version, en_us),
            ),
            (
                "AND e.mc_version<>? AND e.en_us=? ORDER BY e.id LIMIT 96",
                (mc_version, en_us),
            ),
            (
                "AND e.mc_version<>? AND e.en_us<>? ORDER BY e.mc_version, e.id LIMIT 96",
                (mc_version, en_us),
            ),
        )
        candidates: list[tuple] = []
        seen_ids: set[int] = set()
        for suffix, tier_params in tier_queries:
            for row in self.db._q(
                base_sql + suffix,
                (
                    self.kind,
                    content_mod_value(self.kind, self.mod_id),
                    content_key_value(self.kind, self.key),
                    *tier_params,
                ),
            ):
                row_id = int(row[0])
                if row_id not in seen_ids:
                    seen_ids.add(row_id)
                    candidates.append(row)
        consistent = [
            row for row in candidates if self._quality_details(row)[0] == "consistent"
        ]
        lower_quality = [
            row for row in candidates if self._quality_details(row)[0] != "consistent"
        ]
        return (consistent + lower_quality)[:24]

    def _same_key_context(self, rows: list[tuple]) -> list[dict[str, Any]]:
        """把同 key 的已審核列轉成 context 項目，並標示版本與衝突限制。"""
        mc_version, en_us = self.mc_version, self.en_us
        same_version_rows = [row for row in rows if str(row[2]) == mc_version]
        same_version_translations = {
            str(row[6]).strip()
            for row in same_version_rows
            if self._quality_details(row)[0] == "consistent" and str(row[6]).strip()
        }
        version_conflict = len(same_version_translations) > 1
        context: list[dict[str, Any]] = []
        for row in rows:
            same_text = str(row[5]) == en_us
            same_version = str(row[2]) == mc_version
            limitations = "same module/key and reviewed local source"
            if not same_version:
                limitations = "different Minecraft version; context only"
            elif not same_text:
                limitations = "same key but different English source; context only"
            elif version_conflict:
                limitations = (
                    "conflicting reviewed translations; human decision required"
                )
            context.append(
                self._db_context_item(
                    row,
                    reference_type="same_key_reviewed",
                    match_type=(
                        "exact_key_same_text" if same_text else "different_source_text"
                    ),
                    confidence=(
                        "high"
                        if same_version and same_text and not version_conflict
                        else "medium"
                    ),
                    limitations=limitations,
                )
            )
        return context

    # -- 同模組 reviewed glossary --------------------------------------------

    @staticmethod
    def _lookup_phrases(normalized_source: str, source_words: list[str]) -> list[str]:
        """建立有界的 n-gram 精確查詢詞組。"""
        lookup_phrases: list[str] = []
        if normalized_source:
            lookup_phrases.append(normalized_source)
        # 有界 n-gram 精確查詢補足 bulk LIMIT；因此 id > 5000 的
        # reviewed lang 專有名詞仍能被找回，不需要掃描整張 glossary。
        for width in range(min(6, len(source_words)), 0, -1):
            for start in range(len(source_words) - width + 1):
                phrase = " ".join(source_words[start : start + width]).strip()
                if len(phrase) >= 2 and phrase not in lookup_phrases:
                    lookup_phrases.append(phrase)
                if len(lookup_phrases) >= 96:
                    break
            if len(lookup_phrases) >= 96:
                break
        return lookup_phrases

    def _glossary_target_rows(self, lookup_phrases: list[str]) -> list[tuple]:
        """以詞組精確查詢 reviewed lang 列，目前版本排在其他版本之前。"""
        if not lookup_phrases:
            return []
        self._bulk_glossary_rows()
        scope = (*self._glossary_cache_scope(), self.mc_version)
        if self.cache.get("glossary_complete", {}).get(scope):
            # bulk glossary 沒被上限截斷時，詞組命中列必定是它的子集；
            # 直接取用快取，不必每筆候選再各查一次資料庫。
            return self._target_rows_from_bulk(scope, lookup_phrases)
        if self._glossary_scope() is not None:
            rows = self._scoped_target_rows(lookup_phrases)
            if rows is not None:
                return rows
        return self._glossary_target_rows_from_sql(lookup_phrases)

    def _glossary_target_rows_from_sql(self, lookup_phrases: list[str]) -> list[tuple]:
        """以模組或範圍條件直接查資料庫取詞組命中列（無快取可用時的查詢）。"""
        mc_version = self.mc_version
        placeholders = ",".join("?" for _ in lookup_phrases)
        scope_sql, scope_params = self._glossary_scope_sql()
        kinds = self._glossary_kinds()
        # 分成目前版本與其他版本兩段查詢，讓 glossary_order 索引直接
        # 提供 LIMIT 前的順序；不要用 CASE 排序把整批候選放進暫存 B-tree。
        current = self._language_rows(
            "glossary_target_current_rows",
            scope_sql + " AND e.mc_version=? "
            "AND e.en_us IN (" + placeholders + ") "
            "AND f.review_status='reviewed' AND trim(f.zh_tw)<>'' "
            "ORDER BY e.id LIMIT 96",
            (*scope_params, mc_version, *lookup_phrases),
            order_key=lambda row: int(row[0]),
            kinds=kinds,
        )
        other = self._language_rows(
            "glossary_target_other_rows",
            scope_sql + " AND e.mc_version<>? "
            "AND e.en_us IN (" + placeholders + ") "
            "AND f.review_status='reviewed' AND trim(f.zh_tw)<>'' "
            "ORDER BY e.mc_version, e.id LIMIT 96",
            (*scope_params, mc_version, *lookup_phrases),
            order_key=lambda row: (str(row[2]), int(row[0])),
            kinds=kinds,
        )
        return current + other

    def _scoped_target_rows(self, lookup_phrases: list[str]) -> list[tuple] | None:
        """模組包範圍的詞組命中列：詞組先走 en_us 索引，排序與上限在記憶體完成。

        索引在舊資料庫不存在時回傳 ``None``，由呼叫端改用一般查詢。
        """
        phrases = list(dict.fromkeys(lookup_phrases))
        scope_sql, scope_params = self._glossary_scope_sql()
        sql = _scoped_phrase_sql(len(phrases), scope_sql)
        rows: list[tuple] = []
        for kind in self._glossary_kinds():
            try:
                rows.extend(self.db._q(sql, (*phrases, kind, *scope_params)))
            except sqlite3.OperationalError:
                return None
        return self._cap_target_rows(rows)

    def _cap_target_rows(self, rows: list[tuple]) -> list[tuple]:
        """依資料庫查詢的順序與上限整理詞組命中列。

        目前版本依 id、其他版本依（版本, id）排序，每個 kind 各取最多
        ``_TARGET_ROW_LIMIT`` 列，目前版本在前。
        """
        current_by_kind: dict[str, list[tuple]] = {}
        other_by_kind: dict[str, list[tuple]] = {}
        for row in rows:
            target = (
                current_by_kind if str(row[2]) == self.mc_version else other_by_kind
            )
            target.setdefault(str(row[1]), []).append(row)

        def current_key(item: tuple) -> int:
            return int(item[0])

        def other_key(item: tuple) -> tuple[str, int]:
            return (str(item[2]), int(item[0]))

        current = sorted(
            (
                row
                for kind_rows in current_by_kind.values()
                for row in sorted(kind_rows, key=current_key)[:_TARGET_ROW_LIMIT]
            ),
            key=current_key,
        )
        other = sorted(
            (
                row
                for kind_rows in other_by_kind.values()
                for row in sorted(kind_rows, key=other_key)[:_TARGET_ROW_LIMIT]
            ),
            key=other_key,
        )
        return current + other

    def _target_rows_from_bulk(
        self, scope: tuple[Any, ...], lookup_phrases: list[str]
    ) -> list[tuple]:
        """由完整的 bulk glossary 取詞組命中列，結果與資料庫查詢逐列相同。

        索引用去重前的原始列建立：同一筆條目有多筆已審核譯文時，資料庫查詢會回傳
        每一列，這裡也要保留。
        """
        by_en_cache = self.cache.setdefault("glossary_by_en", {})
        if scope not in by_en_cache:
            by_en: dict[str, list[tuple]] = {}
            for row in self.cache["glossary_raw_rows"][scope]:
                by_en.setdefault(str(row[5]), []).append(row)
            by_en_cache[scope] = by_en
        by_en = by_en_cache[scope]
        rows = [
            row
            for phrase in dict.fromkeys(lookup_phrases)
            for row in by_en.get(phrase, ())
        ]
        return self._cap_target_rows(rows)

    def _bulk_glossary_rows(self) -> tuple[tuple, ...]:
        """取得同模組／版本共用的 bulk glossary（最多 5,000 列，已去重）。"""
        mc_version = self.mc_version
        scope_sql, scope_params = self._glossary_scope_sql()
        kinds = self._glossary_kinds()
        current_rows = self._language_rows(
            "glossary_current_rows",
            scope_sql + " AND e.mc_version=? "
            "AND f.review_status='reviewed' AND e.en_us<>'' "
            "AND trim(f.zh_tw)<>'' ORDER BY e.id LIMIT 5000",
            (*scope_params, mc_version),
            order_key=lambda row: int(row[0]),
            limit=5000,
            kinds=kinds,
        )
        remaining = 5000 - len(current_rows)
        other_rows = (
            self._language_rows(
                "glossary_other_rows",
                scope_sql + " AND e.mc_version<>? "
                "AND f.review_status='reviewed' AND e.en_us<>'' "
                "AND trim(f.zh_tw)<>'' ORDER BY e.mc_version, e.id LIMIT ?",
                (*scope_params, mc_version, remaining),
                order_key=lambda row: (str(row[2]), int(row[0])),
                limit=remaining,
                kinds=kinds,
            )
            if remaining
            else []
        )
        # 同一模組／版本的候選會共用相同 bulk glossary。先把目前版本與
        # 其他版本合併一次，再建立詞元倒排索引；每筆候選只取可能命中的列，
        # 之後仍交給 contains_term 做最後的詞界確認。
        scope = (*self._glossary_cache_scope(), mc_version)
        rows_cache = self.cache.setdefault("glossary_rows", {})
        if scope not in rows_cache:
            merged: list[tuple] = []
            seen_ids: set[int] = set()
            for row in current_rows + other_rows:
                row_id = int(row[0])
                if row_id not in seen_ids:
                    seen_ids.add(row_id)
                    merged.append(row)
            rows_cache[scope] = tuple(merged)
            # 兩段查詢都沒碰到上限才代表同範圍的已審核列全數在記憶體中。
            self.cache.setdefault("glossary_complete", {})[scope] = (
                len(current_rows) < 5000 and len(other_rows) < remaining
            )
            self.cache.setdefault("glossary_raw_rows", {})[scope] = tuple(
                current_rows + other_rows
            )
        return rows_cache[scope]

    def _glossary_term_index(
        self, glossary_rows: tuple[tuple, ...]
    ) -> tuple[dict[str, list[tuple]], dict[str, list[tuple]], dict[int, int]]:
        """建立（並快取）詞元倒排索引，避免每筆候選重掃最多 5,000 列。"""
        scope = (*self._glossary_cache_scope(), self.mc_version)
        index_cache = self.cache.setdefault("glossary_term_index", {})
        if scope not in index_cache:
            indexed_tokens: dict[str, list[tuple]] = {}
            fallback_rows: dict[str, list[tuple]] = {}
            row_order = {int(row[0]): index for index, row in enumerate(glossary_rows)}
            for row in glossary_rows:
                normalized_term = term_canonical_key(
                    normalize_terminology_text(str(row[5])).strip()
                )
                if not normalized_term:
                    continue
                tokens = tuple(re.findall(r"[A-Za-z0-9]+", normalized_term))
                if tokens:
                    for token in set(tokens):
                        indexed_tokens.setdefault(token, []).append(row)
                else:
                    fallback_rows.setdefault(normalized_term[:3], []).append(row)
            index_cache[scope] = (indexed_tokens, fallback_rows, row_order)
        return index_cache[scope]

    def _glossary_rows_for_item(self) -> list[tuple]:
        """選出可能與目前原文相關的 glossary 列：目標詞組命中者在前。"""
        normalized_source = normalize_terminology_text(self.en_us).strip()
        source_words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’:+._/-]*", normalized_source)
        lookup_phrases = self._lookup_phrases(normalized_source, source_words)
        glossary_target_rows = self._glossary_target_rows(lookup_phrases)
        glossary_rows = self._bulk_glossary_rows()
        indexed_tokens, fallback_rows, row_order = self._glossary_term_index(
            glossary_rows
        )
        source_canonical = term_canonical_key(normalized_source)
        source_tokens = tuple(
            dict.fromkeys(re.findall(r"[A-Za-z0-9]+", source_canonical))
        )
        token_candidates = [
            indexed_tokens[token] for token in source_tokens if token in indexed_tokens
        ]
        relevant_glossary: dict[int, tuple] = {}
        if token_candidates:
            # 取所有詞元桶的聯集；只選最小桶會漏掉同一句中的其他獨立術語。
            for rows in token_candidates:
                for row in rows:
                    relevant_glossary[int(row[0])] = row
        else:
            source_prefixes = {
                source_canonical[index : index + width]
                for width in (1, 2, 3)
                for index in range(len(source_canonical) - width + 1)
            }
            for prefix in source_prefixes:
                for row in fallback_rows.get(prefix, ()):
                    relevant_glossary[int(row[0])] = row
        relevant_target: list[tuple] = []
        relevant_ids: set[int] = set()
        for row in glossary_target_rows:
            normalized_term = term_canonical_key(
                normalize_terminology_text(str(row[5])).strip()
            )
            target_tokens = tuple(
                dict.fromkeys(re.findall(r"[A-Za-z0-9]+", normalized_term))
            )
            if not target_tokens or all(
                token in source_tokens for token in target_tokens
            ):
                relevant_target.append(row)
                relevant_ids.add(int(row[0]))
        for row in relevant_target:
            relevant_glossary[int(row[0])] = row
        return relevant_target + sorted(
            (
                row
                for row_id, row in relevant_glossary.items()
                if row_id not in relevant_ids
            ),
            key=lambda row: row_order[int(row[0])],
        )

    def _official_context(self, official: Any) -> list[dict[str, Any]]:
        """建立內建官方 Minecraft 在地化的術語與完整翻譯 evidence。"""
        return [
            evidence_context_entry(from_official_match(match))
            for match in official.official_terminology_matches_for_item(
                {"path": self.key, "source_text": self.en_us, "text": self.en_us}
            )
        ]

    def _glossary_context(
        self, glossary_rows_for_item: list[tuple], official: Any
    ) -> list[dict[str, Any]]:
        """把命中原文詞界且通過官方術語規則的 glossary 列轉成 context。"""
        key, en_us, mc_version = self.key, self.en_us, self.mc_version
        reviewed_entries = [
            {
                "key": str(row[4]),
                "en_us": str(row[5]),
                "zh_tw": str(row[6]),
            }
            for row in glossary_rows_for_item
            if self._quality_details(row)[0] == "consistent"
        ]
        terminology_plan = terminology_terms_for_item(
            {
                "path": key,
                "source_text": en_us,
                "_reviewed_glossary": reviewed_entries,
            },
            index=official,
        )
        selected_pairs = {
            (
                term_canonical_key(normalize_terminology_text(source)),
                target.strip(),
            )
            for source, target in terminology_plan
        }
        selected_sources = [
            (
                normalize_terminology_text(source),
                term_canonical_key(normalize_terminology_text(source)),
            )
            for source, _target in terminology_plan
        ]
        normalized_en_us = normalize_terminology_text(en_us)
        matched_glossary = []
        for row in glossary_rows_for_item:
            if self._quality_details(row)[0] != "consistent":
                continue
            normalized_term = normalize_terminology_text(str(row[5])).strip()
            if not contains_term(normalized_en_us, normalized_term):
                continue
            if not terminology_term_allowed(
                en_us,
                normalized_term,
                key=str(row[4]),
                index=official,
            ):
                continue
            matched_glossary.append(row)
        by_term: dict[str, list[tuple]] = {}
        for row in matched_glossary:
            normalized_term = normalize_terminology_text(str(row[5])).strip()
            by_term.setdefault(term_canonical_key(normalized_term), []).append(row)
        context: list[dict[str, Any]] = []
        for term in sorted(by_term, key=lambda value: (-len(value), value))[:12]:
            term_rows = by_term[term]
            same_version_term_rows = [
                row for row in term_rows if str(row[2]) == mc_version
            ]
            conflict_rows = same_version_term_rows or term_rows
            term_targets = {str(row[6]).strip() for row in conflict_rows}
            term_conflict = len(term_targets) != 1
            if (
                not term_conflict
                and (term, next(iter(term_targets))) not in selected_pairs
            ):
                continue
            if term_conflict and any(
                selected_key != term and contains_term(selected_source, term)
                for selected_source, selected_key in selected_sources
            ):
                continue
            for row in term_rows[:4]:
                normalized_term = normalize_terminology_text(str(row[5])).strip()
                context.append(
                    self._db_context_item(
                        row,
                        reference_type="reviewed_lang_glossary",
                        match_type="whole_term_same_mod",
                        confidence=(
                            "high"
                            if str(row[2]) == mc_version and not term_conflict
                            else "medium"
                        ),
                        limitations=(
                            "conflicting reviewed glossary terms; not a unique answer"
                            if term_conflict
                            else "same-module reviewed lang term; applies to this text only"
                            if str(row[2]) == mc_version
                            else "different Minecraft version; context only"
                        ),
                        en_us_value=normalized_term,
                    )
                )
        return context

    # -- 模組包項目退回 ``@`` 前面模組名稱的 lang ----------------------------------

    def _name_fallback_index(
        self,
    ) -> tuple[dict[str, list[tuple]], tuple[str, ...], dict[str, Any]]:
        """``@`` 前面模組名稱在目前版本的已審核 lang 列與詞組前綴索引。

        每個（模組, 版本）只查詢與建立一次；每筆候選只用前綴索引找出可能命中的術語，
        不會逐列比對整個模組的 lang（候選數 × lang 列數會讓大量候選慢上數十倍）。
        """
        base_name = self.mod_id.split("@", 1)[0]
        cache = self.cache.setdefault("lang_name_fallback", {})
        scope = (base_name, self.mc_version)
        if scope not in cache:
            rows = (
                self.db._q(
                    _REVIEWED_ENTRY_COLUMNS
                    + "WHERE e.kind=? AND e.mod_id=? AND e.mc_version=? "
                    "AND f.review_status='reviewed' AND trim(f.zh_tw)<>'' "
                    "AND e.en_us<>f.zh_tw",
                    (KIND_LANG, base_name, self.mc_version),
                )
                if base_name
                else []
            )
            by_term: dict[str, list[tuple]] = {}
            for row in rows:
                term = normalize_terminology_text(str(row[5])).strip()
                if term:
                    by_term.setdefault(term, []).append(row)
            terms = tuple(by_term)
            cache[scope] = (by_term, terms, build_term_prefix_index(terms))
        return cache[scope]

    def _lang_name_fallback_context(
        self, official: Any, same_kind_context: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """KubeJS／FTB 項目：同包沒有的術語，退回 ``@`` 前面模組名稱的 lang。

        只補同包（同種類）glossary 還沒有的原文；譯法不唯一的術語不提供。
        """
        if self._glossary_scope() is None or "@" not in self.mod_id:
            return []
        by_term_rows, terms, prefix_index = self._name_fallback_index()
        if not terms:
            return []
        covered = {
            term_canonical_key(normalize_terminology_text(str(item.get("en_us", ""))))
            for item in same_kind_context
        }
        normalized_en_us = normalize_terminology_text(self.en_us)
        matched_terms = matching_terms(
            (normalized_en_us,), terms, prefix_index=prefix_index
        )
        by_canonical: dict[str, list[tuple]] = {}
        for term in matched_terms:
            canonical = term_canonical_key(term)
            if canonical in covered:
                continue
            for row in by_term_rows[term]:
                if not self._locale_ok(row):
                    continue
                if self._quality_details(row)[0] != "consistent":
                    continue
                if not terminology_term_allowed(
                    self.en_us, term, key=str(row[4]), index=official
                ):
                    continue
                by_canonical.setdefault(canonical, []).append(row)
        context: list[dict[str, Any]] = []
        for canonical in sorted(by_canonical, key=lambda value: (-len(value), value))[
            :6
        ]:
            rows = sorted(by_canonical[canonical], key=lambda row: int(row[0]))
            if len({str(row[6]).strip() for row in rows}) != 1:
                continue
            row = rows[0]
            context.append(
                self._db_context_item(
                    row,
                    reference_type="reviewed_lang_glossary",
                    match_type="whole_term_module_name_lang",
                    confidence="medium",
                    limitations=(
                        "reviewed lang of the module name before '@'; "
                        "used because the pack has no reviewed term; applies to this text only"
                    ),
                    en_us_value=normalize_terminology_text(str(row[5])).strip(),
                )
            )
        return context

    # -- 同文與模組自帶語系 ---------------------------------------------------

    def _same_text_context(self) -> list[dict[str, Any]]:
        """跨模組同英文原文的已審核譯文，僅作輔助參考。"""
        related_rows = self._cached_rows(
            "same_text_rows",
            _REVIEWED_ENTRY_COLUMNS
            + "WHERE e.en_us=? AND e.id<>? AND f.review_status='reviewed' "
            "AND trim(f.zh_tw)<>'' ORDER BY e.mod_id, e.kind, e.key, e.id LIMIT 12",
            (self.en_us, self.entry_id),
        )
        context: list[dict[str, Any]] = []
        for row in related_rows:
            if not self._locale_ok(row):
                continue
            if (
                str(row[1]) == self.kind
                and str(row[3]) == self.mod_id
                and str(row[4]) == self.key
            ):
                continue
            context.append(
                self._db_context_item(
                    row,
                    reference_type="same_text_reviewed",
                    match_type="same_text",
                    confidence="medium",
                    limitations="cross-module auxiliary reference; not this module's canonical name",
                )
            )
        return context

    def _module_locale_context(self) -> list[dict[str, Any]]:
        """同條目由模組自帶 zh_tw.json 提供、尚未人工審核的譯文。"""
        entry_id, en_us = self.entry_id, self.en_us
        module_locale_context: list[dict[str, Any]] = []
        for zh_tw, source, review_status, revision in self._cached_rows(
            "module_locale_rows",
            "SELECT zh_tw, source, review_status, revision "
            "FROM translation WHERE entry_id=? AND source=? "
            "AND trim(zh_tw)<>'' "
            "AND (review_status IS NULL OR review_status<>'reviewed') "
            "ORDER BY revision DESC LIMIT 2",
            (entry_id, SRC_JAR_TW),
        ):
            value = str(zh_tw).strip()
            if not value or value == str(en_us).strip():
                continue
            module_locale_context.append(
                {
                    "id": f"module-locale:{entry_id}:source:{source}:revision:{revision or '-'}",
                    "reference_type": "module_locale",
                    "entry_id": entry_id,
                    "mod_id": self.mod_id,
                    "kind": self.kind,
                    "key": self.key,
                    "mc_version": self.mc_version,
                    "en_us": en_us,
                    "zh_tw": value,
                    "source_text": en_us,
                    "target_text": value,
                    "review_status": review_status or "unreviewed",
                    "source": source,
                    "source_label": self.db.source_catalog.label(source),
                    "match_type": "same_entry_unreviewed",
                    "confidence": "low",
                    "limitations": "模組自帶 zh_tw.json，尚未人工審核；僅供參考，不是官方標準",
                }
            )
        return module_locale_context


class SmartReviewContextMixin:
    """提供給 TranslationDB 使用的智慧校對 local context 快照。"""

    def _smart_review_context(
        self,
        entry_id: int,
        kind: str,
        mc_version: str,
        mod_id: str,
        key: str,
        en_us: str,
    ) -> list[dict[str, Any]]:
        """建立可重播且有界的本地術語與翻譯證據快照。"""
        cache = getattr(self, "_smart_review_context_cache", {})
        return _LocalContextBuilder(
            self,
            cache,
            entry_id=entry_id,
            kind=kind,
            mc_version=mc_version,
            mod_id=mod_id,
            key=key,
            en_us=en_us,
        ).build()
