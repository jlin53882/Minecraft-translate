"""Mod 資料庫機翻的同原文合併：分組規則、上限、寫回同組條目、預覽與日誌文字。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services_impl import moddb_service, moddb_translate_service
from app.services_impl.moddb_translate_merge import merge_key, select_merged_rows
from app.services_impl.moddb_translate_service import TranslateOptions
from app.views.moddb.operation_summaries import (
    translation_preview_target_text,
    translation_scope_text,
    translation_written_delta,
)
from tests.test_moddb_translate import _known_lang_item, run
from translation_tool.translation_db import DbSettings, ScanItem, TranslationDB
from translation_tool.translation_db.schema import KIND_KUBEJS_LANG, KIND_LANG, SRC_AI

PACK_85 = "All the Mods 10 8.5@aaaa"
PACK_86 = "All the Mods 10 8.6@bbbb"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "tr.db"
    monkeypatch.setattr(
        moddb_service,
        "load_db_settings",
        lambda: DbSettings(path=str(path), version="1.21.1"),
    )
    return path


@pytest.fixture(autouse=True)
def _no_real_cache(monkeypatch):
    """不碰真正的翻譯快取。"""
    monkeypatch.setattr(
        moddb_translate_service,
        "add_to_cache_with_receipt",
        lambda *a, **k: SimpleNamespace(accepted=True, changed=True),
    )
    monkeypatch.setattr(
        moddb_translate_service, "save_translation_cache", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_shared_loop.reload_translation_cache",
        lambda *a, **k: None,
    )


def _pack_item(namespace: str, pack: str, key: str, en_us: str) -> ScanItem:
    return ScanItem(
        KIND_KUBEJS_LANG, f"{namespace}@{pack}", key, en_us, pack_scope=pack
    )


# ------------------------------------------------------------------ 分組規則
def test_merge_key_ignores_pack_version_but_not_kind_text_or_locale():
    base = merge_key((1, KIND_KUBEJS_LANG, f"ars@{PACK_85}", "a", "Hat"), None)
    assert base == merge_key((2, KIND_KUBEJS_LANG, f"ars@{PACK_86}", "b", "Hat"), None)
    # 預設語系與無法確認的語系同一組（與批次分組規則一致）
    assert base == merge_key(
        (3, KIND_KUBEJS_LANG, f"ars@{PACK_85}", "c", "Hat"), "en_us"
    )
    assert base != merge_key((4, KIND_KUBEJS_LANG, f"ars@{PACK_85}", "d", "hat"), None)
    assert base != merge_key(
        (5, KIND_KUBEJS_LANG, f"other@{PACK_85}", "e", "Hat"), None
    )
    assert base != merge_key(
        (6, KIND_KUBEJS_LANG, f"ars@{PACK_85}", "f", "Hat"), "pt_br"
    )
    # 一般 lang 的 mod_id 就是模組識別；不同類型不合併
    assert merge_key((7, KIND_LANG, "ars", "g", "Hat"), None) != base


def test_select_merged_rows_limit_counts_groups_and_keeps_all_twins():
    rows = [
        (1, KIND_LANG, "foo", "a", "Gear"),
        (2, KIND_LANG, "foo", "b", "Plate"),
        (3, KIND_LANG, "foo", "c", "Gear"),
        (4, KIND_LANG, "foo", "d", "Rod"),
        (5, KIND_LANG, "foo", "e", "Plate"),
    ]
    selection = select_merged_rows(rows, {}, limit=2)
    # 上限 2 ＝ 前兩組（Gear、Plate）；第三組 Rod 不納入，但前兩組排在後面的同組條目仍納入
    assert [row[0] for row in selection.rows] == [1, 2]
    assert selection.twins_of(1) == (rows[2],)
    assert selection.twins_of(2) == (rows[4],)
    assert (selection.ai_count, selection.entry_count, selection.merged_count) == (
        2,
        4,
        2,
    )
    assert selection.twins_of(4) == () and selection.twins_of(None) == ()

    unlimited = select_merged_rows(rows, {}, limit=0)
    assert unlimited.ai_count == 3 and unlimited.entry_count == 5


# ------------------------------------------------------------------ 服務層
def _seed_duplicates(path) -> None:
    db = TranslationDB(path)
    db.ingest(
        "1.21.1",
        [
            _known_lang_item("foo", "item.foo.a", "Gear"),
            _known_lang_item("foo", "item.foo.b", "Gear"),
            _known_lang_item("foo", "item.foo.c", "Plate"),
            # 不同模組的相同原文不合併
            _known_lang_item("bar", "item.bar.a", "Gear"),
            _pack_item("ars", PACK_85, "item.ars.hat", "Cinder Hat"),
            _pack_item("ars", PACK_86, "item.ars.hat", "Cinder Hat"),
        ],
    )
    db.close()


def _counting_engine(monkeypatch, translator):
    sent: list[str] = []

    def fake_batch(batch, total=None, dry_run=False):
        sent.extend(item["source_text"] for item in batch)
        return [{**it, "text": translator(it["source_text"])} for it in batch], "DONE"

    monkeypatch.setattr(moddb_translate_service, "translate_batch_smart", fake_batch)
    return sent


def test_service_sends_each_text_once_and_writes_the_whole_group(db_path, monkeypatch):
    _seed_duplicates(db_path)
    sent = _counting_engine(monkeypatch, lambda t: "翻:" + t)
    snap = run(TranslateOptions(version="1.21.1", limit=0))
    summary = snap["summary"]

    assert sorted(sent) == ["Cinder Hat", "Gear", "Gear", "Plate"]  # foo、bar 各一次
    assert summary["candidates"] == 6 and summary["ai_items"] == 4
    assert summary["merged"] == 2
    assert summary["written"] == 6 and summary["remaining"] == 0
    db = TranslationDB(db_path)
    rows = {(r.mod_id, r.key): r for r in db.list_entries("1.21.1", limit=100)[0]}
    assert rows[("foo", "item.foo.b")].zh_tw == "翻:Gear"
    assert rows[("foo", "item.foo.b")].source == SRC_AI
    assert rows[(f"ars@{PACK_86}", "item.ars.hat")].zh_tw == "翻:Cinder Hat"
    db.close()

    texts = [e.text for e in snap["logs"]]
    assert any("🔗 相同原文合併：6 筆條目只送 AI 4 筆" in t for t in texts)
    assert any("寫入 6/6" in t and "相同原文合併" in t for t in texts)


def test_rejected_representative_leaves_the_whole_group_unwritten(db_path, monkeypatch):
    _seed_duplicates(db_path)
    # Gear 的譯文多了換行 → 品質檢查拒絕；同組的 foo.b 也不能寫入
    _counting_engine(monkeypatch, lambda t: "翻:" + t + ("\n" if t == "Gear" else ""))
    summary = run(TranslateOptions(version="1.21.1", mod_ids=("foo",), limit=0))[
        "summary"
    ]
    assert summary["ai_items"] == 2 and summary["merged"] == 1
    assert summary["flagged"] == 2 and summary["written"] == 1
    assert summary["remaining"] == 2
    db = TranslationDB(db_path)
    rows = {r.key: r for r in db.list_entries("1.21.1", limit=100)[0]}
    assert rows["item.foo.a"].zh_tw == "" and rows["item.foo.b"].zh_tw == ""
    assert rows["item.foo.c"].zh_tw == "翻:Plate"
    # 同組條目也列進待人工確認，預填同一個 AI 譯文
    flagged = {int(k): v for k, v in summary["flagged_entries"].items()}
    assert flagged == {
        rows["item.foo.a"].id: "翻:Gear\n",
        rows["item.foo.b"].id: "翻:Gear\n",
    }
    db.close()


def test_source_fallback_representative_leaves_twins_untranslated(db_path, monkeypatch):
    _seed_duplicates(db_path)

    def fake_batch(batch, total=None, dry_run=False):
        # Gear 這組 AI 沒給譯文（退回原文）；Plate 正常
        return [
            {**it, "text": it["source_text"], "_untranslated": True}
            if it["source_text"] == "Gear"
            else {**it, "text": "翻:" + it["source_text"]}
            for it in batch
        ], "DONE"

    monkeypatch.setattr(moddb_translate_service, "translate_batch_smart", fake_batch)
    summary = run(TranslateOptions(version="1.21.1", mod_ids=("foo",), limit=0))[
        "summary"
    ]
    assert summary["written"] == 1 and summary["flagged"] == 0
    db = TranslationDB(db_path)
    rows = {r.key: r.zh_tw for r in db.list_entries("1.21.1", limit=100)[0]}
    assert rows["item.foo.a"] == "" and rows["item.foo.b"] == ""
    assert rows["item.foo.c"] == "翻:Plate"
    db.close()


def test_small_limit_reads_a_window_but_still_collects_twins(db_path, monkeypatch):
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            _pack_item("ars", PACK_85, "item.ars.a", "Hat"),
            _pack_item("ars", PACK_85, "item.ars.b", "Cape"),
            _pack_item("ars", PACK_85, "item.ars.c", "Boots"),
            _pack_item("ars", PACK_86, "item.ars.z", "Hat"),  # 窗口外的同組條目
            _pack_item("zen", PACK_85, "item.zen.a", "Hat"),  # 不同模組，不合併
            _known_lang_item("foo", "item.foo.a", "Hat"),
            _known_lang_item("foo", "item.foo.b", "Gear"),
            # 填充列：範圍要夠大，小上限才會只讀前綴
            *(
                _known_lang_item("zzz", f"item.zzz.{i:02d}", f"Fill {i}")
                for i in range(40)
            ),
        ],
    )
    fetches: list = []
    original = db.untranslated_entries

    def spy(version, mods, limit=None, **kwargs):
        fetches.append((tuple(mods or ()), limit))
        return original(version, mods, limit, **kwargs)

    monkeypatch.setattr(db, "untranslated_entries", spy)
    full = select_merged_rows(
        original("1.21.1", None, None, exclude_reusable=False),
        {},
    )
    for limit in (1, 2, 3, 4, 5):
        fetches.clear()
        selection, _locales = moddb_translate_service._load_merged_selection(
            db, "1.21.1", (), limit, exclude_reusable=False, kind=None
        )
        expected = select_merged_rows(
            original("1.21.1", None, None, exclude_reusable=False), {}, limit
        )
        assert selection.rows == expected.rows
        assert dict(selection.twins) == dict(expected.twins)
        # 有上限時不讀整個範圍：第一次只讀前綴
        assert fetches[0][1] == limit * 2
    # 上限 1：窗口只有 ars 的 8.5 版前兩列，同組的 8.6 版條目仍要收進來
    selection, _ = moddb_translate_service._load_merged_selection(
        db, "1.21.1", (), 1, exclude_reusable=False, kind=None
    )
    assert [r[3] for r in selection.twins_of(selection.rows[0][0])] == ["item.ars.z"]
    assert full.ai_count == 46
    db.close()


def test_limit_counts_ai_items_and_preview_reports_the_merge(db_path, monkeypatch):
    _seed_duplicates(db_path)
    sent = _counting_engine(monkeypatch, lambda t: "翻:" + t)
    snap = run(TranslateOptions(version="1.21.1", dry_run=True, limit=1))
    summary = snap["summary"]
    assert not sent
    # 查詢順序第一組是 ars 的 Cinder Hat（kubejs_lang 排在 lang 前），兩個模組包版本同一組
    assert summary["ai_items"] == 1 and summary["candidates"] == 2
    assert summary["merged"] == 1
    texts = [e.text for e in snap["logs"]]
    assert any(
        "本次將送 AI 翻譯 1 筆" in t and "相同原文合併：2 筆條目只送 AI 1 筆" in t
        for t in texts
    )
    assert any("單次上限送 AI 1 筆" in t for t in texts)


def test_estimate_scope_plan_counts_merged_ai_items(db_path):
    _seed_duplicates(db_path)
    db = TranslationDB(db_path)
    ai_count, batches = moddb_translate_service.estimate_scope_plan(
        db, version="1.21.1", reuse_other_versions=False
    )
    assert ai_count == 4 and batches >= 1
    db.close()


def test_count_untranslated_merged_matches_the_merge_groups(db_path):
    _seed_duplicates(db_path)
    db = TranslationDB(db_path)
    # foo Gear×2、foo Plate、bar Gear、ars Cinder Hat×2（兩個模組包版本）→ 4 組
    assert db.count_untranslated("1.21.1") == 6
    assert db.count_untranslated_merged("1.21.1") == 4
    assert db.count_untranslated_merged("1.21.1", ["foo"]) == 2
    assert db.count_untranslated_merged("1.21.1", kind=KIND_KUBEJS_LANG) == 1
    db.close()


def test_scope_text_shows_merged_estimate_before_preview(db_path):
    from app.views.moddb import translate_panel
    from tests.conftest import mock_page

    _seed_duplicates(db_path)
    db = TranslationDB(db_path)
    panel = translate_panel.TranslatePanel(mock_page(), lambda: db)
    panel.refresh_scope()
    panel.limit_field.value = "0"
    panel._refresh_counts()
    text = panel.count_text.value
    assert "此範圍有 6 筆未翻譯（相同原文合併後約 4 筆要送 AI）" in text
    assert "本次送 AI 翻譯全部 4 筆" in text
    panel.limit_field.value = "3"
    panel._refresh_counts()
    assert "本次送 AI 翻譯最多 3 筆" in panel.count_text.value
    db.close()


# ------------------------------------------------------------------ 畫面文字
def test_scope_text_uses_preview_counts_after_preview():
    before = translation_scope_text(10, 0, 0, 10, "預估約 1 批")
    assert "送 AI 翻譯全部 10 筆（相同原文只送一次；預估約 1 批）" in before
    after = translation_scope_text(
        10, 0, 0, 10, "預覽規劃 1 批", preview_counts=(6, 10)
    )
    assert "本次送 AI 翻譯 6 筆（相同原文合併，涵蓋 10 筆條目；預覽規劃 1 批）" in after
    no_dup = translation_scope_text(10, 0, 0, 10, "預估約 1 批", merged_groups=10)
    assert "相同原文合併後" not in no_dup
    same = translation_scope_text(10, 0, 5, 5, "預覽規劃 1 批", preview_counts=(5, 5))
    assert "上限送 AI 5 筆" in same and "本次送 AI 翻譯 5 筆（預覽規劃 1 批）" in same


def test_kpi_deltas_mention_merge_only_when_it_happened():
    assert translation_preview_target_text(10, 10) == "本次將翻譯 10 筆"
    assert (
        translation_preview_target_text(10, 6)
        == "本次將翻譯 10 筆（相同原文合併，送 AI 6 筆）"
    )
    assert translation_written_delta(4, 0) == "AI 回傳 4 筆"
    assert translation_written_delta(4, 2) == "AI 回傳 4 筆（相同原文合併 2 筆）"


def test_select_merged_rows_skips_whitespace_only_source():
    rows = [
        (1, KIND_LANG, "foo", "a", "  \n"),
        (2, KIND_LANG, "foo", "b", "Gear"),
        (3, KIND_LANG, "foo", "c", " "),
    ]
    selection = select_merged_rows(rows, {}, limit=0)
    assert [row[0] for row in selection.rows] == [2]
    assert selection.entry_count == 1
