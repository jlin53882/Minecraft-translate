"""批次機翻「包含其他原文」：把沒有譯文的其他原文接成候選列。

其他原文是「鍵值相同、原文與主原文不同」的另一份原文（``translation_db/variants.py``）。勾選
「包含其他原文」時，沒有譯文的其他原文與沒有譯文的條目一起成為候選，翻完存回其他原文
（來源 AI 機翻，只供流水線查詢）。

候選列沿用條目的格式 ``(編號, 類型, 模組, 鍵值, 原文)``：其他原文用**負的變體編號**當編號，
所以合併相同原文（``moddb_translate_merge``）、分批、品質檢查與寫回都不必改格式。寫回時
``TranslationDB.write_back`` 看到「原文與條目的主原文不同」就會把譯文存成該條目的其他原文，
不需要另外的寫回路徑。負編號不是條目，不能在「條目校對」開啟，所以不列進待人工確認的條目。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services_impl.moddb_translate_merge import EntryRow


def is_variant_row(entry_id: object) -> bool:
    """候選編號是不是其他原文（負的變體編號）。"""
    return isinstance(entry_id, int) and entry_id < 0


def variant_candidate_rows(
    db,
    version: str,
    mod_ids: Sequence[str],
    kind: str | None,
) -> tuple[list[EntryRow], dict[int, str | None]]:
    """沒有譯文的其他原文候選列與它們的來源語系（沿用所屬條目的語系）。"""
    found = db.untranslated_variant_entries(version, list(mod_ids), kind=kind)
    if not found:
        return [], {}
    parent_locales = db.source_locales_for_entries(
        sorted({int(entry_id) for _vid, entry_id, *_rest in found})
    )
    rows: list[EntryRow] = []
    locales: dict[int, str | None] = {}
    for variant_id, entry_id, row_kind, mod_id, key, en_us in found:
        rows.append((-int(variant_id), row_kind, mod_id, key, en_us))
        locales[-int(variant_id)] = parent_locales.get(int(entry_id))
    return rows, locales


def count_scope_untranslated(
    db,
    version: str,
    mod_ids: Sequence[str],
    kind: str | None,
    *,
    include_variants: bool,
) -> int:
    """範圍內仍未翻譯的筆數：條目，加上勾選時的其他原文。"""
    total = db.count_untranslated(version, list(mod_ids), kind=kind)
    if include_variants:
        total += db.count_untranslated_variants(version, list(mod_ids), kind=kind)
    return total
