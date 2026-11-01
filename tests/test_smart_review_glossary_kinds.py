"""同模組已審核的語系術語，是否注入各種來源 kind 的智慧校對 context。"""

from __future__ import annotations

from translation_tool.translation_db import (
    KIND_FTB_QUEST_LANG,
    KIND_FTB_QUESTS,
    KIND_KUBEJS_LANG,
    KIND_KUBEJS_SCRIPT,
    KIND_LANG,
    ScanItem,
    TranslationDB,
)

V = "1.21.1"


def _reviewed_term_context(lang_kind, lang_mod, cand_kind, cand_mod, cand_key, cand_en):
    """建立一個已審核的語系術語與一個候選條目，回傳候選的 glossary 注入結果。"""
    db = TranslationDB(_tmp_db_path())
    try:
        db.ingest(
            V, [ScanItem(lang_kind, lang_mod, "term.valve", "Quantum Valve", "量子閥")]
        )
        for row in db.list_entries(V, limit=None)[0]:
            db.review_manual(row.id, expected_zh_tw=row.zh_tw, propagate=False)
        db.ingest(V, [ScanItem(cand_kind, cand_mod, cand_key, cand_en, "", "")])
        cand = next(r for r in db.list_entries(V, limit=None)[0] if r.kind == cand_kind)
        context = db._smart_review_context(
            cand.id, cand_kind, V, cand.mod_id, cand.key, cand.en_us
        )
        return {
            (item["en_us"], item["zh_tw"])
            for item in context
            if item["reference_type"] == "reviewed_lang_glossary"
        }
    finally:
        db.close()


def _tmp_db_path():
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp()) / "glossary.db"


def test_plain_lang_candidate_gets_same_module_reviewed_term():
    pairs = _reviewed_term_context(
        KIND_LANG, "demo", KIND_LANG, "demo", "item.x", "Install the Quantum Valve"
    )
    assert ("Quantum Valve", "量子閥") in pairs


def test_kubejs_script_candidate_gets_reviewed_term_when_namespace_matches():
    pairs = _reviewed_term_context(
        KIND_KUBEJS_LANG,
        "ns",
        KIND_KUBEJS_SCRIPT,
        "ns",
        "scripts/a.js|t",
        "Install the Quantum Valve",
    )
    assert ("Quantum Valve", "量子閥") in pairs


def test_ftb_quest_candidate_gets_reviewed_ftb_lang_term():
    pairs = _reviewed_term_context(
        KIND_FTB_QUEST_LANG,
        "ftbquests",
        KIND_FTB_QUESTS,
        "ftbquests",
        "chapters/a.snbt|task",
        "Install the Quantum Valve",
    )
    assert ("Quantum Valve", "量子閥") in pairs


def test_terms_from_other_modules_are_not_injected():
    pairs = _reviewed_term_context(
        KIND_FTB_QUEST_LANG,
        "ftbquests",
        KIND_FTB_QUESTS,
        "other",
        "chapters/b.snbt|task",
        "Install the Quantum Valve",
    )
    assert pairs == set()
