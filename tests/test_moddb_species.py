"""Mod DB 學名候選掃描的服務與 UI 控制測試。"""

from __future__ import annotations

from pathlib import Path

from app.services_impl import moddb_species_service
from app.views.moddb.entries_panel import EntriesPanel
from app.views.moddb.species_dialog import SpeciesCandidateDialog
from tests.conftest import mock_page
from translation_tool.translation_db import KIND_LANG, ScanItem, TranslationDB


def _seed(path: Path) -> TranslationDB:
    db = TranslationDB(path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "plants", "item.agave", "Agave tequilana"),
            ScanItem(KIND_LANG, "plants", "item.akebia", "Akebia quinata"),
            ScanItem(KIND_LANG, "plants", "item.cucumber", "Cucumis sativus"),
            ScanItem(KIND_LANG, "plants", "item.steel", "Steel Casing"),
        ],
    )
    return db


def test_scan_species_candidates_keeps_entry_scope_and_filters_values(tmp_path):
    db = _seed(tmp_path / "mod.db")
    try:
        result = moddb_species_service.scan_species_candidates(
            db, "1.21.1", mod_id="plants", kind=KIND_LANG
        )
    finally:
        db.close()

    assert result.scanned_entries == 4
    assert result.total_candidates == 3
    assert [candidate.value for candidate in result.candidates] == [
        "Agave tequilana",
        "Akebia quinata",
        "Cucumis sativus",
    ]
    assert all(candidate.mod_id == "plants" for candidate in result.candidates)
    assert result.truncated is False


def test_scan_species_candidates_reports_limit_without_writing(tmp_path):
    db = _seed(tmp_path / "mod.db")
    try:
        result = moddb_species_service.scan_species_candidates(db, "1.21.1", limit=2)
        assert result.total_candidates == 2
        assert result.truncated is True
        assert db.count_entries() == 4
    finally:
        db.close()


def test_species_dialog_exposes_scan_and_lookup_actions(tmp_path):
    db = _seed(tmp_path / "mod.db")
    try:
        dialog = SpeciesCandidateDialog(
            mock_page(), lambda: db, "1.21.1", "plants", KIND_LANG
        )
        assert dialog.scan_btn.content == "掃描候選"
        assert dialog.query_all_btn.content == "查詢全部候選"
        assert dialog.query_all_btn.disabled is True
    finally:
        db.close()


def test_entries_panel_adds_species_scan_button(tmp_path):
    db = _seed(tmp_path / "mod.db")
    try:
        panel = EntriesPanel(mock_page(), lambda: db)
        assert panel.species_scan_btn.content == "掃描學名"
    finally:
        db.close()
