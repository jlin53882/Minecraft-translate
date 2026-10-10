"""Mod 資料庫批次機翻：資料庫查詢、服務層（假翻譯引擎）與頁籤。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services_impl import moddb_service, moddb_translate_service
from app.services_impl.moddb_translate_service import (
    TranslateOptions,
    build_items,
    run_moddb_translate_service,
)
from app.tasks.task_session import TaskSession
from app.views.moddb import retranslation_controller, translate_panel
from tests.conftest import mock_filepicker, mock_page
from translation_tool.translation_db import (
    KIND_LANG,
    DbSettings,
    ScanItem,
    TranslationDB,
)
from translation_tool.translation_db.models import WriteBackItem
from translation_tool.translation_db.schema import (
    KIND_PATCHOULI,
    SRC_AI,
    SRC_JAR_TW,
    SRC_MANUAL,
)


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
def fake_cache(monkeypatch):
    """不碰真正的翻譯快取：記錄 add_to_cache 被呼叫的內容。"""
    added: list[tuple] = []
    monkeypatch.setattr(
        moddb_translate_service,
        "add_to_cache",
        lambda ctype, key, src, dst, **kw: added.append((ctype, key, src, dst)) or True,
    )
    monkeypatch.setattr(
        moddb_translate_service, "save_translation_cache", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_shared_loop.reload_translation_cache",
        lambda *a, **k: None,
    )
    return added


def seed(path):
    db = TranslationDB(path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing", "鋼製外殼"),
            ScanItem(KIND_LANG, "foo", "item.foo.b", "Infused Alloy"),
            ScanItem(KIND_LANG, "foo", "item.foo.c", "Line one\n"),
            ScanItem(KIND_LANG, "bar", "item.bar.a", "Bronze Gear"),
        ],
    )
    # 另一版本已有 bar.a 的譯文 → 應被沿用，不必呼叫 AI
    db.ingest(
        "1.20.1", [ScanItem(KIND_LANG, "bar", "item.bar.a", "Bronze Gear", "青銅齒輪")]
    )
    db.close()


def fake_engine(monkeypatch, translator):
    """把機翻引擎換成假的：translator(text) -> 譯文。"""

    def fake_batch(batch, total=None, dry_run=False):
        return [{**it, "text": translator(it["source_text"])} for it in batch], "DONE"

    monkeypatch.setattr(moddb_translate_service, "translate_batch_smart", fake_batch)


def run(options):
    session = TaskSession()
    run_moddb_translate_service(options, session)
    return session.snapshot()


def test_repository_counts_and_lists_only_untranslated(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    assert db.count_untranslated("1.21.1") == 3
    assert db.count_untranslated("1.21.1", ["foo"]) == 2
    rows = db.untranslated_entries("1.21.1", limit=2)
    assert [(r[2], r[3]) for r in rows] == [
        ("bar", "item.bar.a"),
        ("foo", "item.foo.b"),
    ]  # 依模組、鍵值排序且受 limit 限制
    db.close()


def test_reuse_from_other_versions_fills_without_ai(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    assert db.reuse_from_other_versions("1.21.1") == 1
    rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
    assert rows["item.bar.a"].zh_tw == "青銅齒輪"
    assert rows["item.bar.a"].source == SRC_JAR_TW  # 沿用時保留原來源標記
    assert db.count_untranslated("1.21.1") == 2
    db.close()


def test_build_items_carries_entry_identity():
    items = build_items([(7, KIND_LANG, "foo", "item.foo.b", "Infused Alloy")])
    assert items[0]["_entry_id"] == 7 and items[0]["cache_type"] == "lang"
    assert (
        items[0]["path"] == "item.foo.b" and items[0]["source_text"] == "Infused Alloy"
    )


def test_service_translates_and_writes_back_as_ai(db_path, monkeypatch):
    seed(db_path)
    fake_engine(monkeypatch, lambda t: "翻:" + t)
    snap = run(TranslateOptions(version="1.21.1"))
    assert snap["status"].upper() == "DONE"
    summary = snap["summary"]
    assert summary["reused"] == 1
    assert summary["written"] == 2 and summary["flagged"] == 0
    assert summary["remaining"] == 0
    db = TranslationDB(db_path)
    rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
    assert rows["item.foo.b"].zh_tw == "翻:Infused Alloy"
    assert rows["item.foo.b"].source == SRC_AI
    assert rows["item.foo.a"].zh_tw == "鋼製外殼"  # 既有譯文不被動
    db.close()


def test_service_does_not_write_when_special_chars_differ(db_path, monkeypatch):
    seed(db_path)
    # 假引擎把結尾換行弄丟 → 該筆不應寫入
    fake_engine(monkeypatch, lambda t: "翻:" + t.strip())
    summary = run(TranslateOptions(version="1.21.1", mod_ids=("foo",)))["summary"]
    assert summary["flagged"] == 1 and summary["written"] == 1
    db = TranslationDB(db_path)
    rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
    assert rows["item.foo.c"].zh_tw == ""
    assert rows["item.foo.b"].zh_tw.startswith("翻:")
    db.close()


def test_service_limit_and_dry_run(db_path, monkeypatch):
    seed(db_path)
    called = []
    monkeypatch.setattr(
        moddb_translate_service,
        "translate_batch_smart",
        lambda *a, **k: called.append(1) or ([], "DONE"),
    )
    summary = run(TranslateOptions(version="1.21.1", dry_run=True, limit=1))["summary"]
    assert summary["dry_run"] is True and summary["candidates"] == 1
    assert summary["remaining"] == 3 and not called
    db = TranslationDB(db_path)
    assert db.count_untranslated("1.21.1") == 3  # 預覽不寫入也不沿用
    db.close()


def test_service_reports_error_without_database(db_path):
    snap = run(TranslateOptions(version="1.21.1"))  # 資料庫檔案不存在
    assert snap["status"].upper() == "ERROR"


def test_translate_panel_scope_and_options(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    panel = translate_panel.TranslatePanel(mock_page(), lambda: db)
    panel.refresh_scope()
    assert panel.version_dd.value == "1.21.1"
    assert "3" in panel.count_text.value
    panel.mod_dd.value = "foo"
    panel.limit_field.value = "1"
    panel._refresh_counts()
    opts = panel.build_options()
    assert opts.mod_ids == ("foo",) and opts.limit == 1 and opts.reuse_other_versions
    panel.mod_dd.value = translate_panel.ALL_MODS
    assert panel.build_options().mod_ids == ()
    panel.version_dd.value = None
    panel.start_clicked()
    assert panel.status_chip.label.value == "請先選擇遊戲版本"
    db.close()


def test_translate_panel_runs_and_shows_summary(db_path, monkeypatch):
    seed(db_path)
    fake_engine(monkeypatch, lambda t: "翻:" + t)
    monkeypatch.setattr(
        retranslation_controller,
        "launch_standalone_worker",
        lambda target: target(),
    )
    monkeypatch.setattr(
        translate_panel.PollerHandle, "start", lambda self, page, handler: True
    )
    db = TranslationDB(db_path)
    panel = translate_panel.TranslatePanel(mock_page(), lambda: db)
    panel.refresh_scope()
    panel.start_clicked()
    panel.sync_from_session()
    assert panel.status_chip.label.value == "機翻完成"
    assert panel.stat_written.value_text.value == "2"
    db.close()


def test_list_entries_filters_by_effective_source_after_manual_review(db_path):
    """審核改變 effective source，但保留 AI row；count、搜尋與分頁一致。"""
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "foo",
                f"item.reviewed.{index:02d}",
                "Minecraft",
                "Minecraft",
                source=SRC_AI,
            )
            for index in range(7)
        ],
    )
    ai_rows, ai_total = db.list_entries("1.21.1", source=SRC_AI, state="same")
    assert ai_total == 7 and len(ai_rows) == 7
    target = next(row for row in ai_rows if row.key.endswith("00"))

    # 明確審核會新增已審核人工列，並保留原 AI 譯文。
    db.review_manual(
        target.id, expected_zh_tw="Minecraft", actor="測試審核", propagate=False
    )

    ai_rows, ai_total = db.list_entries("1.21.1", source=SRC_AI)
    manual_rows, manual_total = db.list_entries("1.21.1", source=SRC_MANUAL)
    assert ai_total == 6 and target.id not in {row.id for row in ai_rows}
    assert manual_total == 1 and [row.id for row in manual_rows] == [target.id]
    assert manual_rows[0].source == SRC_MANUAL

    # state=manual 與 source=人工一致；與 AI 或「其他譯文」組合時不矛盾。
    assert db.list_entries("1.21.1", state="manual", source=SRC_MANUAL)[1] == 1
    assert db.list_entries("1.21.1", state="manual", source=SRC_AI)[1] == 0
    assert db.list_entries("1.21.1", state="ok", source=SRC_MANUAL)[1] == 0

    # same-source 使用 effective text，因此同文人工項目只會落在人工來源篩選。
    assert db.list_entries("1.21.1", state="same", source=SRC_AI)[1] == 6
    assert db.list_entries("1.21.1", state="same", source=SRC_MANUAL)[1] == 1
    assert db.list_entries("1.21.1", source=SRC_AI, query="reviewed.00") == ([], 0)
    found, found_total = db.list_entries(
        "1.21.1", source=SRC_MANUAL, query="reviewed.00"
    )
    assert found_total == 1 and [row.id for row in found] == [target.id]

    # AI source filter 的 count 和分頁 rows 共用相同 predicate，無重複或漏項。
    seen_ids = []
    for offset in (0, 2, 4):
        page, total = db.list_entries(
            "1.21.1", source=SRC_AI, state="same", limit=2, offset=offset
        )
        assert total == 6
        seen_ids.extend(row.id for row in page)
    assert len(seen_ids) == 6 and len(set(seen_ids)) == 6
    assert set(seen_ids) == {row.id for row in ai_rows}

    # 低優先序 AI row 仍保存，供右側「各來源譯文」列表展示。
    detail = db.entry_detail(target.id)
    translations = {row.source: row.zh_tw for row in detail.translations}
    assert translations == {SRC_AI: "Minecraft", SRC_MANUAL: "Minecraft"}
    db.close()


def test_entries_panel_source_filter(db_path):
    from app.views.moddb import entries_panel
    from translation_tool.translation_db.schema import SRC_JAR_TW

    seed(db_path)
    db = TranslationDB(db_path)
    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
    assert panel.source_filter.dropdown.label == "目前生效來源"
    panel.refresh()
    assert panel.total == 4
    panel.source_filter.dropdown.value = str(SRC_JAR_TW)
    panel._on_source()
    assert panel.total == 1  # 只有 foo.a 有「模組自帶繁中」
    panel.source_filter.dropdown.value = "__all__"
    panel._on_source()
    assert panel.total == 4
    db.close()


def test_entries_panel_source_filter_tracks_reviewed_effective_source(db_path):
    from app.views.moddb import entries_panel
    from tests.test_moddb_view import texts_of

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "foo",
                "item.reviewed",
                "Minecraft",
                "Minecraft",
                source=SRC_AI,
            )
        ],
    )
    entry = db.list_entries("1.21.1")[0][0]
    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
    panel.refresh()
    panel.source_filter.dropdown.value = str(SRC_AI)
    panel._on_source()
    assert panel.total == 1

    db.review_manual(
        entry.id,
        expected_zh_tw="Minecraft",
        actor="測試審核",
        propagate=False,
    )
    panel._on_source()
    assert panel.total == 0

    panel.source_filter.dropdown.value = str(SRC_MANUAL)
    panel._on_source()
    assert panel.total == 1 and [row.id for row in panel.rows] == [entry.id]
    panel.select(entry.id)
    shown_sources = "\n".join(texts_of(panel.history_col))
    assert "AI 機翻" in shown_sources and "人工" in shown_sources
    db.close()


def test_entries_panel_save_and_review_use_separate_manual_states(db_path):
    from app.views.moddb.entries_panel import EntriesPanel
    from tests.test_moddb_view import texts_of

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG, "foo", "item.review", "Minecraft", "Minecraft", source=SRC_AI
            )
        ],
    )
    page = mock_page()
    page.run_thread = lambda target: target()
    panel = EntriesPanel(page, lambda: db)
    panel.refresh()
    panel.tw_field.value = "人工修改"
    panel._update_impact()
    panel._save()
    page._run_all_tasks()
    detail = db.entry_detail(panel.selected.id)
    manual = next(row for row in detail.translations if row.source == SRC_MANUAL)
    assert manual.review_status == "unreviewed" and manual.checker == ""
    assert "人工-未審核" in "\n".join(texts_of(panel))
    assert panel.confirm_btn.visible

    panel.tw_field.value = ""
    panel._on_text_change()
    assert panel.confirm_btn.visible is False and panel.confirm_btn.disabled is True
    panel.tw_field.value = panel.selected.zh_tw
    panel._on_text_change()
    assert panel.confirm_btn.visible is True and panel.confirm_btn.disabled is False

    panel._confirm()
    assert page.overlay and page.overlay[-1].title.value == "確認審核範圍"
    page.overlay[-1].actions[-1].on_click(None)
    detail = db.entry_detail(panel.selected.id)
    manual = next(row for row in detail.translations if row.source == SRC_MANUAL)
    assert manual.review_status == "reviewed"
    assert detail.history[0].action == "review"
    assert "人工-已審核" in "\n".join(texts_of(panel))
    assert panel.confirm_btn.visible is False
    db.close()


def test_blank_db_path_is_remembered_when_database_is_created(tmp_path, monkeypatch):
    """路徑空白時，第一次建立資料庫會把實際路徑寫進 config；已填的路徑不被覆蓋。"""
    from translation_tool.translation_db import settings as db_settings

    saved: list[dict] = []
    monkeypatch.setattr(
        "translation_tool.utils.config_manager.load_config",
        lambda *a, **k: {"translation_db": {"path": ""}},
    )
    monkeypatch.setattr(
        "translation_tool.utils.config_manager.save_config",
        lambda cfg, *a, **k: saved.append(cfg) or True,
    )
    target = tmp_path / "sub" / "mod_translation.db"
    monkeypatch.setattr(DbSettings, "resolved_path", lambda self: target)
    target.parent.mkdir()

    db = db_settings.open_db(DbSettings(path=""), create=True)
    assert db is not None
    db.close()
    assert saved and saved[0]["translation_db"]["path"] == str(target)

    saved.clear()
    other = tmp_path / "again.db"
    monkeypatch.setattr(DbSettings, "resolved_path", lambda self: other)
    db = db_settings.open_db(DbSettings(path="custom.db"), create=True)
    db.close()
    assert saved == []  # 使用者自己填過路徑 → 不覆蓋


def test_db_location_banner_shows_folder_and_status(db_path):
    from app.views.config.db_location import DbLocationBanner
    from tests.test_moddb_view import texts_of

    banner = DbLocationBanner()
    assert banner.folder_text.value == str(db_path.parent)
    assert "找不到" in texts_of(banner.status_box)
    seed(db_path)
    banner.refresh()
    assert "已建立" in texts_of(banner.status_box)


def test_set_dropdown_options_dedupes_and_skips_when_unchanged():
    from app.ui import kit

    dd = kit.dropdown(label="x")
    assert kit.set_dropdown_options(dd, [("a", "A"), ("b", "B"), ("a", "A2")])
    assert [(o.key, o.text) for o in dd.options] == [("a", "A"), ("b", "B")]
    first = list(dd.options)
    assert kit.set_dropdown_options(dd, [("a", "A"), ("b", "B")]) is False
    assert dd.options == first  # 內容沒變 → 同一批物件，不重建
    assert kit.set_dropdown_options(dd, [("a", "A")])
    assert len(dd.options) == 1


def test_entries_version_dropdown_has_no_duplicates_after_repeated_refresh(db_path):
    from app.views.moddb import entries_panel

    seed(db_path)
    db = TranslationDB(db_path)
    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
    for _ in range(3):
        panel.refresh()
    keys = [o.key for o in panel.version_dd.options]
    assert keys == ["1.21.1", "1.20.1"] and len(keys) == len(set(keys))
    db.close()


def test_same_key_suggestion_offers_apply_even_when_source_differs():
    """同鍵值、其他版本的原文不同時也能「套用」（只是帶入輸入框，不自動儲存）。"""
    from types import SimpleNamespace as NS

    from app.views.moddb.suggestions import build_suggestions

    def apply_buttons(control):
        found = []
        if getattr(control, "content", None) == "套用":
            found.append(control)
        for attr in ("controls", "content"):
            child = getattr(control, attr, None)
            if isinstance(child, list):
                for c in child:
                    found.extend(apply_buttons(c))
            elif child is not None and not isinstance(child, str):
                found.extend(apply_buttons(child))
        return found

    applied: list[str] = []
    detail = NS(
        entry=NS(zh_tw="", en_us="Distribution Interval [ticks]"),
        same_key=[
            NS(
                mc_version="1.20.1",
                source=3,
                zh_tw="分配間隔 [ticks]",
                same_text=False,
                en_us="",  # 由翻譯 ZIP 匯入：只有鍵值與譯文，沒有原文
            ),
            NS(
                mc_version="1.19.2",
                source=None,
                zh_tw="",
                same_text=False,
                en_us="",
            ),
        ],
        same_text=[],
    )
    controls = build_suggestions(detail, "key", applied.append)
    buttons = apply_buttons(controls[0])
    assert len(buttons) == 1
    buttons[0].on_click(None)
    assert applied == ["分配間隔 [ticks]"]
    assert apply_buttons(controls[1]) == []  # 沒有譯文的版本不提供套用

    # 其他版本的原文要直接顯示出來供對照；沒有原文標「原文未知」，有但不同標「原文不同」
    from tests.test_moddb_view import texts_of

    first = texts_of(controls[0])
    assert any("原文：（未知" in t for t in first) and "原文未知" in first
    detail.same_key.append(
        NS(
            mc_version="1.18.2",
            source=1,
            zh_tw="舊譯",
            same_text=False,
            en_us="Distribution Interval",
        )
    )
    third = texts_of(build_suggestions(detail, "key", applied.append)[2])
    assert "原文：Distribution Interval" in third and "原文不同" in third
    detail.same_key[2].same_text = True
    assert "原文不同" not in texts_of(
        build_suggestions(detail, "key", applied.append)[2]
    )


def test_changed_filter_lists_entries_with_a_pending_source_change(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    # 再掃一次，foo.a 的原文改了 → 記為 src_change（資料庫保留舊原文）
    db.ingest(
        "1.21.1", [ScanItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing II", "新")]
    )
    rows, total = db.list_entries("1.21.1", state="changed")
    assert total == 1 and rows[0].key == "item.foo.a"
    assert rows[0].en_us == "Steel Casing"
    detail = db.entry_detail(rows[0].id)
    assert [(c.old_en, c.new_en) for c in detail.src_changes] == [
        ("Steel Casing", "Steel Casing II")
    ]
    assert db.list_entries("1.21.1", state="all")[1] == 4
    db.close()


def test_overview_stat_cards_jump_to_filtered_entries(db_path):
    from app.views.moddb import overview_panel

    seed(db_path)
    db = TranslationDB(db_path)
    jumps: list[tuple] = []
    panel = overview_panel.OverviewPanel(
        mock_page(), lambda: db, open_entries=lambda *a: jumps.append(a)
    )
    panel.refresh()
    panel.diff_btn.on_click(None)
    panel.changed_btn.on_click(None)
    assert jumps == [("diff", None, None), ("changed", None, None)]
    db.close()


def test_overview_uses_dynamic_effective_source_catalog_and_priority(db_path):
    import sqlite3

    from app.views.moddb.overview_panel import OverviewPanel
    from tests.test_moddb_view import texts_of

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG, "foo", "item.subtitle", "Subtitle", "字幕譯文", source=3
            ),
            ScanItem(KIND_LANG, "foo", "item.custom", "Custom", "自訂譯文", source=100),
            ScanItem(KIND_LANG, "foo", "item.jar", "Jar", "內建譯文", source=1),
            ScanItem(KIND_LANG, "foo", "item.manual.unreviewed", "Manual A", "舊譯文"),
            ScanItem(KIND_LANG, "foo", "item.manual.reviewed", "Manual B", "舊譯文"),
            ScanItem(KIND_LANG, "foo", "item.manual.legacy", "Manual C", "舊譯文"),
            ScanItem(KIND_LANG, "foo", "item.untranslated", "Empty", ""),
        ],
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('custom_sources', ?)",
            (json.dumps({"釘宮翻譯組": 100}, ensure_ascii=False),),
        )
    db.close()
    db = TranslationDB(db_path)
    for key, text in (
        ("item.manual.unreviewed", "人工待審核"),
        ("item.manual.reviewed", "人工已審核"),
        ("item.manual.legacy", "歷史人工"),
    ):
        entry = next(row for row in db.list_entries("1.21.1")[0] if row.key == key)
        db.save_manual(entry.id, text, propagate=False)
        if key == "item.manual.reviewed":
            db.review_manual(entry.id, expected_zh_tw=text, propagate=False)
        elif key == "item.manual.legacy":
            db._conn.execute(
                "UPDATE translation SET review_status='legacy_unknown' "
                "WHERE entry_id=? AND source=?",
                (entry.id, SRC_MANUAL),
            )
            db._conn.execute(
                "UPDATE effective SET review_status='legacy_unknown' WHERE entry_id=?",
                (entry.id,),
            )
            db._conn.commit()

    db.set_priority((100, SRC_MANUAL, 1, 3))
    panel = OverviewPanel(mock_page(), lambda: db)
    panel.refresh()
    labels = texts_of(panel.legend)
    assert "釘宮翻譯組" in labels
    assert "釘宮翻譯組（自訂 #100）" in labels
    assert "人工-未審核" in labels
    assert "人工-已審核" in labels
    assert "人工（歷史狀態待確認）" in labels
    assert "未翻譯" in labels
    assert labels.index("釘宮翻譯組（自訂 #100）") < labels.index("模組自帶繁中")

    bar = panel.versions_col.controls[0].controls[1].content
    assert (
        sum(segment.expand for segment in bar.controls) == db.version_stats()[0].total
    )
    assert any("未翻譯：1" in segment.tooltip for segment in bar.controls)

    db.set_priority((3, SRC_MANUAL, 1, 100))
    panel.refresh()
    labels = texts_of(panel.legend)
    assert labels.index("釘宮翻譯組") < labels.index("人工-未審核")
    assert labels.index("人工（歷史狀態待確認）") < labels.index("模組自帶繁中")
    db.close()


def test_entries_panel_changed_filter_and_editor_note(db_path):
    from app.views.moddb import entries_panel
    from tests.test_moddb_view import texts_of

    seed(db_path)
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1", [ScanItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing II", "新")]
    )
    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
    panel.refresh()
    panel.show_filter("changed")
    panel._load_list()
    assert panel.total == 1
    panel.select(panel.rows[0].id)
    assert any("Steel Casing II" in t for t in texts_of(panel.meta_col))
    db.close()


def test_pager_buttons_get_fresh_unique_keys_on_every_render():
    """Flet 比對新舊清單會把內容相同的項目配對，頁數變少時殘留舊頁碼；key 全新才不會配對。"""
    from app.ui.kit.inputs import Pager

    pager = Pager(3650, page_size=50)
    before = [c.key for c in pager.buttons.controls]
    pager.set_state(34, 1)
    after = [c.key for c in pager.buttons.controls]
    assert len(set(before)) == len(before) and len(set(after)) == len(after)
    assert not set(before) & set(after)
    assert len(after) == 3  # ‹ 1 ›：只有一頁


def test_other_translation_filter_never_lists_untranslated_entries(db_path):
    """「其他譯文」篩選不會出現未翻譯條目；切換篩選後清單項目的 key 全新。"""
    from app.views.moddb import entries_panel
    from app.views.moddb.formatting import STATE_LABELS

    assert STATE_LABELS["ok"] == "其他譯文"
    seed(db_path)
    db = TranslationDB(db_path)
    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
    panel.refresh()
    panel.show_filter("none")
    panel._load_list()
    none_keys = {c.key for c in panel.list_view.controls}
    assert {r.zh_tw for r in panel.rows} == {""}
    panel.show_filter("ok")
    panel._load_list()
    assert panel.rows and all(r.zh_tw for r in panel.rows)
    ok_keys = {c.key for c in panel.list_view.controls}
    assert len(ok_keys) == len(panel.rows) and not ok_keys & none_keys
    db.close()


def test_db_path_strips_quotes_and_accepts_a_folder(tmp_path, monkeypatch):
    """檔案總管「複製為路徑」會帶引號；填資料夾時使用其中的預設檔名。"""
    from translation_tool.translation_db.settings import (
        DEFAULT_DB_FILE,
        load_db_settings,
        normalize_db_path,
    )

    target = tmp_path / "data" / "mod_translation.db"
    assert normalize_db_path(f'"{target}"') == str(target)
    assert normalize_db_path(f"  '{target}'  ") == str(target)
    assert normalize_db_path("“x.db”") == "x.db"
    assert normalize_db_path(None) == ""

    settings = load_db_settings({"translation_db": {"path": f'"{target}"'}})
    assert settings.path == str(target) and settings.resolved_path() == target

    folder = tmp_path / "dbdir"
    folder.mkdir()
    folder_settings = DbSettings(path=str(folder))
    assert folder_settings.resolved_path() == folder / DEFAULT_DB_FILE


def test_config_db_path_field_cleans_quotes_and_checks_existence(db_path, monkeypatch):
    """設定頁路徑欄位：貼上帶引號的路徑自動去引號，並即時顯示檔案是否存在。"""
    from app.ui import kit
    from app.views.config.db_location import DbLocationBanner, attach_path_hooks
    from tests.test_moddb_view import texts_of
    from translation_tool.utils import config_manager

    monkeypatch.setattr(
        config_manager,
        "resolve_project_path",
        lambda path: db_path.parent / path,
    )

    field = kit.field(label="資料庫檔案", helper="原本的說明")
    banner = DbLocationBanner()
    check = attach_path_hooks(field, banner)

    seed(db_path)
    field.value = f'"{db_path}"'
    field.on_change(SimpleNamespace(control=field, data=field.value))
    assert field.value == str(db_path)  # 輸入當下就去掉引號
    assert field.helper.startswith("✓ 找到資料庫") and "原本的說明" in field.helper
    assert "已建立" in texts_of(banner.status_box)  # 上方資訊列同步預覽

    missing = db_path.parent / "nope" / "x.db"
    field.value = f"  {missing}  "
    field.on_change(SimpleNamespace(control=field, data=field.value))
    assert field.value == f"  {missing}  "  # 輸入中不動空白（可能還在打字）
    field.on_blur(SimpleNamespace(control=field))
    assert field.value == str(missing)  # 離開欄位才整理前後空白
    assert "找不到" in field.helper
    assert "找不到" in texts_of(banner.status_box)

    field.value = ""
    check()
    assert field.helper.startswith("空白")


def test_source_dropdowns_follow_config_priority(tmp_path, monkeypatch):
    """來源下拉的選項順序跟隨 translation_db.priority；改設定後切回頁籤就會更新。"""
    from app.views.moddb import entries_panel, scan_panel
    from app.views.moddb import source_filter as sf
    from translation_tool.translation_db.schema import (
        SRC_CUSTOM,
        SRC_MANUAL,
        SRC_SUBTITLE,
    )

    path = tmp_path / "p.db"
    state = {"priority": (SRC_SUBTITLE, SRC_MANUAL, SRC_CUSTOM)}

    def fake_settings():
        full = state["priority"] + tuple(
            c for c in range(7) if c not in state["priority"]
        )
        return DbSettings(path=str(path), priority=full)

    monkeypatch.setattr(sf, "current_settings", fake_settings)
    monkeypatch.setattr(scan_panel, "current_settings", fake_settings)
    monkeypatch.setattr(moddb_service, "load_db_settings", fake_settings)

    flt = sf.SourceFilter(lambda: None)
    keys = [o.key for o in flt.dropdown.options]
    assert keys[:4] == ["__all__", str(SRC_SUBTITLE), str(SRC_MANUAL), str(SRC_CUSTOM)]

    state["priority"] = (SRC_MANUAL, SRC_CUSTOM, SRC_SUBTITLE)
    flt.refresh()
    keys = [o.key for o in flt.dropdown.options]
    assert keys[:4] == ["__all__", str(SRC_MANUAL), str(SRC_CUSTOM), str(SRC_SUBTITLE)]

    panel = scan_panel.ScanPanel(mock_page(), mock_filepicker(), lambda: None)
    zip_keys = [o.key for o in panel.source_dd.options]
    assert zip_keys[:3] == [str(SRC_MANUAL), str(SRC_CUSTOM), str(SRC_SUBTITLE)]
    assert entries_panel.SourceFilter is sf.SourceFilter


def test_translate_panel_explains_zero_limit_means_all(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    panel = translate_panel.TranslatePanel(mock_page(), lambda: db)
    panel.refresh_scope()
    assert "不限" in panel.limit_field.label
    panel.limit_field.value = "0"
    panel._on_limit_changed()
    assert "上限為 0（不限）" in panel.count_text.value
    # 3 筆未翻譯中，bar.a 其他版本有相同內容的譯文 → 沿用 1 筆，送 AI 全部 2 筆
    assert "其中約 1 筆其他版本已有相同譯文" in panel.count_text.value
    assert "送 AI 翻譯全部 2 筆" in panel.count_text.value
    panel.limit_field.value = "2"
    panel._on_limit_changed()
    assert "上限 2 筆" in panel.count_text.value
    assert "送 AI 翻譯最多 2 筆" in panel.count_text.value
    panel.reuse_row.value = False  # 關掉沿用：3 筆全部送 AI
    panel.limit_field.value = "0"
    panel._on_limit_changed()
    assert "送 AI 翻譯全部 3 筆" in panel.count_text.value
    assert "其他版本已有相同譯文" not in panel.count_text.value
    db.close()


def test_service_aborts_when_every_item_keeps_failing(db_path, monkeypatch):
    """API 設定有問題時（每筆都回填原文），連續失敗達門檻就中止，不要把全部資料跑完。"""
    seed(db_path)
    monkeypatch.setattr(moddb_translate_service, "ABORT_AFTER_FAILURES", 2)
    calls = []

    def always_fail(batch, total=None, dry_run=False):
        calls.append(len(batch))
        return [{**it, "_untranslated": True} for it in batch], "DONE"

    monkeypatch.setattr(moddb_translate_service, "translate_batch_smart", always_fail)
    monkeypatch.setattr(
        moddb_translate_service,
        "run_translator_skeleton",
        _skeleton_one_per_batch(moddb_translate_service.run_translator_skeleton),
    )
    summary = run(TranslateOptions(version="1.21.1", mod_ids=("foo",)))["summary"]
    assert summary["status"] == "ABORTED" and summary["written"] == 0
    assert len(calls) < 3  # foo 有 3 筆未翻譯，一筆一批時第 2 筆失敗後就停止


def _skeleton_one_per_batch(real, report_progress=False):
    def wrapper(items, **kwargs):
        hooks = kwargs["hooks"]
        translate = kwargs["translate_batch_smart"]
        from types import SimpleNamespace as NS

        processed = 0
        for item in items:
            try:
                translated, _ = translate([item], len(items))
            except BaseException:  # noqa: BLE001 - TaskCancelled 是 BaseException
                return NS(status="CANCELLED", processed=processed, last_error=None)
            for result in translated:
                hooks.on_translated_item(result)
                processed += 1
            hooks.on_batch_flushed()
            if report_progress and hooks.on_progress:
                hooks.on_progress(processed / len(items), "✅ 批次完成", 0.0)
        return NS(status="DONE", processed=processed, last_error=None)

    return wrapper


def test_remote_error_detail_shows_server_message_without_secrets():
    from translation_tool.core.lm_translator_main import _remote_error_detail

    class Resp:
        text = ""

        def json(self):
            return {
                "error": {
                    "message": "Invalid value at 'generation_config' key=AIzaSyA1234567890abcdefghijklmnopqrstuv"
                }
            }

    err = type("E", (Exception,), {})()
    err.response = Resp()
    detail = _remote_error_detail(err)
    assert "generation_config" in detail and "AIzaSy" not in detail
    assert _remote_error_detail(Exception("x")) == ""


def test_service_writes_translation_cache_by_default_and_can_skip(
    db_path, monkeypatch, fake_cache
):
    seed(db_path)
    fake_engine(monkeypatch, lambda t: "翻:" + t)
    run(TranslateOptions(version="1.21.1", mod_ids=("foo",)))
    assert ("lang", "item.foo.b", "Infused Alloy", "翻:Infused Alloy") in fake_cache

    fake_cache.clear()
    db = TranslationDB(db_path)
    with db._tx() as conn:  # 讓 foo.b 回到未翻譯，才會再翻一次
        conn.execute("DELETE FROM translation WHERE source = ?", (SRC_AI,))
        conn.execute("DELETE FROM effective WHERE source = ?", (SRC_AI,))
    db.close()
    run(TranslateOptions(version="1.21.1", mod_ids=("foo",), write_cache=False))
    assert fake_cache == []


def test_translate_panel_cache_switch_defaults_on(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    panel = translate_panel.TranslatePanel(mock_page(), lambda: db)
    assert panel.build_options().write_cache is True
    panel.cache_row.value = False
    assert panel.build_options().write_cache is False
    db.close()


def test_stat_cache_is_reused_until_data_changes_and_survives_reopen(db_path):
    """總覽統計有快取：資料沒變不重算（含重開連線）；寫入後世代遞增、快取失效。"""
    seed(db_path)
    db = TranslationDB(db_path)
    calls = []
    real = db._overview_uncached
    db._overview_uncached = lambda: calls.append(1) or real()
    first = db.overview()
    assert db.overview() == first and len(calls) == 1  # 第二次讀快取

    db2 = TranslationDB(db_path)  # 重開連線（等同重開程式）
    calls2 = []
    real2 = db2._overview_uncached
    db2._overview_uncached = lambda: calls2.append(1) or real2()
    assert db2.overview() == first and calls2 == []

    db2.ingest("1.21.1", [ScanItem(KIND_LANG, "zed", "item.zed.a", "Zed Block")])
    after = db.overview()  # db 這條連線也會發現資料世代變了
    assert len(calls) == 2 and after["mods"] == first["mods"] + 1
    db.close()
    db2.close()


def test_list_counts_are_cached_per_filter_and_invalidated_by_writes(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    counted = []
    real_one = db._one

    def spy(sql, params=()):
        if sql.startswith("SELECT COUNT(*) FROM entry e"):
            counted.append(sql)
        return real_one(sql, params)

    db._one = spy
    assert db.list_entries("1.21.1", state="none")[1] == 3
    db.list_entries("1.21.1", state="none", offset=1)  # 翻頁不重數
    assert len(counted) == 1
    db.list_entries("1.21.1", state="ok")  # 不同條件才重數
    assert len(counted) == 2
    db.write_back(
        "1.21.1", [WriteBackItem(KIND_LANG, "foo", "item.foo.b", "Infused Alloy", "譯")]
    )
    assert db.list_entries("1.21.1", state="none")[1] == 2  # 寫入後快取失效、結果正確
    assert len(counted) == 3
    assert db.count_untranslated("1.21.1") == 2
    db.close()


def test_moddb_view_keeps_the_connection_when_returning_to_the_page(db_path):
    """切回頁面不重開連線（重開會丟掉 SQLite 頁面快取）；設定變了才重開。"""
    from app.views import moddb_view

    seed(db_path)
    view = moddb_view.ModDbView(mock_page(), mock_filepicker())
    first = view.get_db()
    assert first is not None
    view.did_mount()
    assert view.get_db() is first
    view._db_sig = ("changed", ())
    view.did_mount()
    assert view.get_db() is not first


def test_translate_panel_resume_restarts_polling_after_navigating_away(
    db_path, monkeypatch
):
    """換頁會停止輪詢；切回來必須接續，否則任務結束後畫面永遠卡在「取消中／機翻中」。"""
    import asyncio

    seed(db_path)
    started: list[int] = []
    monkeypatch.setattr(
        translate_panel.PollerHandle,
        "start",
        lambda self, page, handler: started.append(1) or True,
    )
    db = TranslationDB(db_path)
    panel = translate_panel.TranslatePanel(mock_page(), lambda: db)
    panel.resume()
    assert started == []  # 沒有任務：不啟動

    panel.session = TaskSession()
    panel._running = True
    panel.will_unmount()  # 換頁：輪詢停止（任務仍在背景執行）
    panel.resume()  # 切回來
    assert started == [1]

    # 輪詢中發生未預期錯誤：恢復按鈕並標明原因，不能永遠卡住
    def boom():
        raise ValueError("render failed")

    panel.sync_from_session = boom
    panel._set_running(True)
    asyncio.run(panel._poll())
    assert panel._running is False and panel.start_btn.disabled is False
    assert "畫面更新失敗" in panel.status_chip.label.value
    db.close()


@pytest.mark.parametrize(
    ("tab", "expected"),
    [
        ("overview", []),
        ("entries", []),
        ("scan", ["scan"]),
        ("translate", ["translate"]),
    ],
)
def test_moddb_view_did_mount_resumes_only_the_current_tab(
    db_path, monkeypatch, tab, expected
):
    """整頁重新掛載：只接續「目前頁籤」的輪詢；沒掛在畫面上的面板不可重新開始更新畫面。"""
    from app.views import moddb_view

    seed(db_path)
    view = moddb_view.ModDbView(mock_page(), mock_filepicker())
    resumed: list[str] = []
    monkeypatch.setattr(view.scan, "resume", lambda: resumed.append("scan"))
    monkeypatch.setattr(view.translate, "resume", lambda: resumed.append("translate"))
    view.tab = tab
    view.did_mount()
    assert resumed == expected


@pytest.fixture
def clean_custom_sources():
    """Compatibility fixture: custom sources no longer mutate global names."""
    yield


def _cfg(path, lines):
    return {"translation_db": {"path": str(path), "priority": lines}}


def test_new_names_in_priority_become_custom_sources(db_path, clean_custom_sources):
    """在「來源優先順序」輸入新名稱 → 登錄為自訂來源（代碼 100 起），並出現在各處選單。"""
    from translation_tool.translation_db.schema import SOURCE_NAMES
    from translation_tool.translation_db.settings import load_db_settings

    seed(db_path)
    lines = ["人工", "測試", "町宮字幕組", "自訂補充", "模組自帶繁中", "i18n 轉換"]
    settings = load_db_settings(_cfg(db_path, lines))
    assert 100 not in SOURCE_NAMES
    assert settings.source_catalog.label(100) == "測試"
    assert settings.priority[:3] == (6, 100, 3)  # 人工、測試、町宮字幕組
    assert set(settings.priority) >= {0, 1, 2, 3, 4, 5, 6, 100}  # 沒列出的仍補在後面

    # 代碼穩定：重讀不變；再新增一個拿到下一個代碼；從設定移除也不會消失或重用代碼
    added = load_db_settings(_cfg(db_path, [*lines, "新來源B"]))
    assert added.source_catalog.label(100) == "測試"
    assert added.source_catalog.label(101) == "新來源B"
    removed = load_db_settings(_cfg(db_path, ["人工"]))
    assert 100 in removed.priority and 101 in removed.priority
    assert removed.priority[-2:] == (100, 101)  # 沒列出的自訂來源依代碼接在最後


def test_source_catalog_resolves_legacy_alias_and_custom_name_collision(
    db_path, clean_custom_sources
):
    import json
    import sqlite3

    from translation_tool.translation_db.schema import SOURCE_NAMES, SRC_SUBTITLE
    from translation_tool.translation_db.settings import load_db_settings

    seed(db_path)
    # Reproduce an existing DB written before subtitle source 3 was renamed.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('custom_sources', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps({"釘宮翻譯組": 100}, ensure_ascii=False),),
        )
    settings = load_db_settings(
        {
            "translation_db": {
                "path": str(db_path),
                "priority": ["町宮字幕組", "釘宮翻譯組", "custom:100"],
                "zip_source": "builtin:subtitle",
            }
        }
    )
    assert settings.priority[:2] == (SRC_SUBTITLE, 100)
    assert settings.zip_source == SRC_SUBTITLE
    assert settings.priority_lines[0] == "町宮字幕組"
    assert settings.source_catalog.label(SRC_SUBTITLE) == "釘宮翻譯組"
    assert settings.source_catalog.label(100) == "釘宮翻譯組（自訂 #100）"
    assert settings.source_catalog.token_for(SRC_SUBTITLE) == "builtin:subtitle"
    assert settings.source_catalog.resolve("builtin:subtitle") == SRC_SUBTITLE
    assert settings.source_catalog.resolve("custom:100") == 100
    legacy_zip = load_db_settings(
        {
            "translation_db": {
                "path": str(db_path),
                "priority": [],
                "zip_source": "町宮字幕組",
            }
        }
    )
    assert legacy_zip.zip_source == SRC_SUBTITLE
    # Built-in names are process-global constants; opening this DB does not leak #100.
    assert SRC_SUBTITLE in SOURCE_NAMES and 100 not in SOURCE_NAMES


def test_renamed_source_display_is_default_and_not_auto_registered(db_path):
    from translation_tool.translation_db.schema import SRC_SUBTITLE
    from translation_tool.translation_db.settings import load_db_settings

    seed(db_path)
    settings = load_db_settings(
        {"translation_db": {"path": str(db_path), "priority": ["釘宮翻譯組"]}}
    )
    assert settings.priority[0] == SRC_SUBTITLE
    assert settings.source_catalog.custom_codes == ()


def test_legacy_source_name_collision_keeps_builtin_and_custom_rows_distinct(
    db_path, clean_custom_sources
):
    import sqlite3

    from translation_tool.translation_db.schema import SRC_SUBTITLE
    from translation_tool.translation_db.settings import load_db_settings

    seed(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('custom_sources', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps({"釘宮翻譯組": 100}, ensure_ascii=False),),
        )
    settings = load_db_settings(
        {
            "translation_db": {
                "path": str(db_path),
                "priority": ["builtin:subtitle", "custom:100"],
            }
        }
    )
    db = TranslationDB(db_path, priority=settings.priority)
    item = WriteBackItem(
        KIND_LANG, "foo", "item.foo.b", "Infused Alloy", "釘宮內建譯文"
    )
    db.write_back("1.21.1", [item], source=SRC_SUBTITLE, fill_other_versions=False)
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", item.key, item.en_us, "自訂版譯文")],
        source=100,
        fill_other_versions=False,
    )
    entry = next(row for row in db.list_entries("1.21.1")[0] if row.key == item.key)
    translations = {
        row.source: row.zh_tw for row in db.entry_detail(entry.id).translations
    }
    assert translations[SRC_SUBTITLE] == "釘宮內建譯文"
    assert translations[100] == "自訂版譯文"
    assert db.source_catalog.label(SRC_SUBTITLE) == "釘宮翻譯組"
    assert db.source_catalog.label(100) == "釘宮翻譯組（自訂 #100）"
    db.close()


def test_custom_source_works_end_to_end(db_path, clean_custom_sources, monkeypatch):
    """自訂來源可寫入譯文、被篩選、顯示名稱，並算進進度條；ZIP 匯入選單也找得到。"""
    from app.services_impl.moddb_source_service import source_label
    from app.views.moddb import entries_panel, scan_panel
    from translation_tool.translation_db.settings import load_db_settings

    seed(db_path)
    cfg = _cfg(db_path, ["人工", "測試"])
    monkeypatch.setattr(
        moddb_service, "load_db_settings", lambda: load_db_settings(cfg)
    )
    code = load_db_settings(cfg).priority[1]
    assert (
        code == 100
        and source_label(code, load_db_settings(cfg).source_catalog) == "測試"
    )

    db = TranslationDB(db_path, priority=load_db_settings(cfg).priority)
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", "item.foo.b", "Infused Alloy", "測試譯文")],
        source=code,
    )
    rows, total = db.list_entries("1.21.1", source=code)
    assert total == 1 and rows[0].source == code and rows[0].zh_tw == "測試譯文"
    stat = next(s for s in db.version_stats() if s.mc_version == "1.21.1")
    assert stat.untranslated == 2 and stat.jar >= 1  # 自訂來源算進藍色段，不會消失

    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
    panel.refresh()
    assert "測試" in [o.text for o in panel.source_filter.dropdown.options]

    scan = scan_panel.ScanPanel(mock_page(), mock_filepicker(), lambda: db)
    assert str(code) in [o.key for o in scan.source_dd.options]
    db.close()


def test_entry_source_filter_hides_unreferenced_custom_sources(db_path, monkeypatch):
    import sqlite3

    from app.views.moddb import entries_panel, scan_panel
    from app.views.moddb.panel_refresh import load_panel_snapshot
    from translation_tool.translation_db.settings import load_db_settings

    seed(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('custom_sources', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (
                json.dumps(
                    {
                        "測試": 100,
                        "釘宮翻譯組": 101,
                        "ModsTranslationPack": 102,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    settings = load_db_settings(_cfg(db_path, ["人工"]))
    monkeypatch.setattr(moddb_service, "load_db_settings", lambda: settings)
    db = TranslationDB(db_path, priority=settings.priority)
    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", "item.foo.b", "Infused Alloy", "測試譯文")],
        source=100,
    )

    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
    snapshot = load_panel_snapshot(
        "entries",
        {
            "version": "1.21.1",
            "mod_id": None,
            "kind": None,
            "page": 1,
            "selected_id": None,
            "criteria": moddb_service.EntryFilter(version="1.21.1"),
        },
        settings,
    )
    assert 100 in snapshot["effective_source_codes"]
    assert 101 not in snapshot["effective_source_codes"]
    assert 102 not in snapshot["effective_source_codes"]
    panel.apply_refresh_snapshot(snapshot)
    option_keys = {option.key for option in panel.source_filter.dropdown.options}
    option_labels = {option.text for option in panel.source_filter.dropdown.options}
    assert "100" in option_keys
    assert "101" not in option_keys
    assert "102" not in option_keys
    assert "釘宮翻譯組（自訂 #101）" not in option_labels
    assert "ModsTranslationPack" not in option_labels

    scan = scan_panel.ScanPanel(mock_page(), mock_filepicker(), lambda: db)
    zip_source_keys = {option.key for option in scan.source_dd.options}
    assert {"100", "101", "102"}.issubset(zip_source_keys)
    db.close()


def test_names_typed_before_the_database_exists_are_registered_on_creation(
    tmp_path, clean_custom_sources
):
    from translation_tool.translation_db.schema import SOURCE_NAMES
    from translation_tool.translation_db.settings import (
        load_db_settings,
        open_db,
        read_custom_sources,
    )

    path = tmp_path / "new" / "x.db"
    cfg = _cfg(path, ["人工", "測試"])
    settings = load_db_settings(cfg)
    assert 100 not in settings.priority  # 資料庫還沒有，無處登錄
    db = open_db(settings, create=True)
    assert db is not None
    assert read_custom_sources(path) == {"測試": 100}
    assert 100 not in SOURCE_NAMES and db.source_catalog.label(100) == "測試"
    assert db.priority[:2] == (6, 100)
    db.close()


def test_priority_field_previews_new_custom_sources(db_path, monkeypatch):
    from app.ui import kit
    from app.views.config.db_location import attach_priority_hooks
    from translation_tool.translation_db.settings import load_db_settings

    seed(db_path)
    monkeypatch.setattr(
        "translation_tool.translation_db.settings.load_db_settings",
        lambda *a, **k: load_db_settings(_cfg(db_path, [])),
    )
    field = kit.field(label="優先序", multiline=True, helper="說明")
    check = attach_priority_hooks(field)
    field.value = "人工\n測試\n新自訂來源"
    field.on_change(SimpleNamespace(control=field, data=field.value))
    assert "將新增自訂來源：測試、新自訂來源" in field.helper and "說明" in field.helper
    assert "釘宮翻譯組" not in field.helper
    field.value = "人工\n町宮字幕組"
    check()
    assert "所有名稱都是已存在的來源" in field.helper


def test_run_progress_estimates_batches_eta_and_formats():
    from translation_tool.translation_db.run_progress import (
        RunProgress,
        estimate_batches,
        format_duration,
        format_live,
    )

    assert format_duration(65) == "1:05" and format_duration(3725) == "1:02:05"
    assert format_duration(None) == "—" and format_duration(-1) == "—"
    assert (
        estimate_batches(
            {"lang": 12725, "patchouli": 250}, lambda k: 300 if k == "lang" else 100
        )
        == 43 + 3
    )

    progress = RunProgress(total=1000, planned_batches=4, started=100.0)
    assert progress.estimated_batches() == 4 and progress.eta_seconds(now=110.0) is None
    assert (
        progress.update(300) is True and progress.update(300) is False
    )  # 沒增加不算新批
    live = progress.live(now=160.0)  # 60 秒做完 300 筆 → 剩 700 筆約 140 秒
    assert live["batch_done"] == 1 and round(live["eta_sec"]) == 140
    assert progress.estimated_batches() == 1 + 3  # 平均每批 300，剩 700 → 再 3 批
    progress.update(450)  # 批次縮小（150 筆）→ 預估總批數增加
    assert progress.estimated_batches() == 2 + 3
    line = format_live(progress.live(now=200.0))
    assert "第 2 / 約 5 批" in line and "已處理 450 / 1,000 筆（45%）" in line
    assert "已用 1:40" in line and "預估剩餘" in line and "完成" in line


def test_service_logs_batches_eta_and_reports_totals(db_path, monkeypatch):
    seed(db_path)
    fake_engine(monkeypatch, lambda t: "翻:" + t)
    # 每批 1 筆 → foo 有 2 筆未翻譯會跑 2 批
    monkeypatch.setattr(
        moddb_translate_service, "_get_default_batch_size", lambda kind, size: 1
    )
    monkeypatch.setattr(
        moddb_translate_service,
        "run_translator_skeleton",
        _skeleton_one_per_batch(None, report_progress=True),
    )
    session = TaskSession()
    run_moddb_translate_service(
        TranslateOptions(version="1.21.1", mod_ids=("foo",)), session
    )
    texts = [e.text for e in session.snapshot()["logs"]]
    assert any("預估約 2 批" in t for t in texts)  # 開始前先告知批數
    assert any(t.startswith("⏱ 第 1 / 約 2 批") for t in texts)
    assert any("第 2 / 約 2 批" in t and "已處理 2 / 2 筆（100%）" in t for t in texts)
    summary = session.snapshot()["summary"]
    assert summary["batches"] == 2 and summary["elapsed_sec"] >= 0
    assert any("共送出 2 批" in t for t in texts)


def test_preview_and_panel_show_batch_estimates_and_live_line(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    panel = translate_panel.TranslatePanel(mock_page(), lambda: db)
    panel.refresh_scope()
    panel.limit_field.value = "0"
    panel._refresh_counts()
    assert "預估約 1 批" in panel.count_text.value  # 3 筆、每批 300 → 1 批

    panel.session = TaskSession()
    panel.session.start()
    panel.session.set_summary(
        {
            "live": {
                "batch_done": 2,
                "batch_est": 5,
                "processed": 600,
                "total": 1500,
                "elapsed_sec": 90.0,
                "eta_sec": 135.0,
                "finish_ts": None,
            }
        }
    )
    panel._running = True
    panel.sync_from_session()
    assert "第 2 / 約 5 批" in panel.live_text.value
    assert "預估剩餘 2:15" in panel.live_text.value
    db.close()


def test_preview_shows_how_many_would_be_reused_and_excludes_them(db_path, monkeypatch):
    """預覽不寫入，但要告訴使用者：多少筆會沿用其他版本（原文相同才算）、多少筆實際送 AI。"""
    seed(db_path)
    db = TranslationDB(db_path)
    # 其他版本同鍵值但「原文不同」→ 不能沿用
    db.ingest(
        "1.20.1",
        [ScanItem(KIND_LANG, "foo", "item.foo.b", "Infused Alloy v1", "舊版譯文")],
    )
    assert db.count_reusable("1.21.1") == 1  # 只有 bar.a（原文相同）
    assert [r[3] for r in db.untranslated_entries("1.21.1", exclude_reusable=True)] == [
        "item.foo.b",
        "item.foo.c",
    ]
    db.close()

    snap = run(TranslateOptions(version="1.21.1", dry_run=True))
    texts = [e.text for e in snap["logs"]]
    assert any(
        "其中約 1 筆其他版本已有相同譯文" in t and "原文不同的不會沿用" in t
        for t in texts
    )
    assert any("本次將送 AI 翻譯 2 筆" in t for t in texts)
    assert snap["summary"]["reused"] == 1
    assert snap["summary"]["candidates"] == 2  # 送 AI 的只有 foo.b、foo.c
    assert snap["summary"]["remaining"] == 3

    off = run(
        TranslateOptions(version="1.21.1", dry_run=True, reuse_other_versions=False)
    )
    assert off["summary"]["reused"] == 0 and off["summary"]["candidates"] == 3


def test_pager_replaces_the_whole_button_row_on_each_render():
    """頁碼列整列換新控制項：同一個 buttons 清單被大幅增減時 Flet 會殘留／整列消失。"""
    from app.ui.kit.inputs import Pager

    pager = Pager(3650, page_size=50)
    first_row = pager.buttons
    assert pager.content.controls[1] is first_row
    pager.set_state(34, 1)
    assert pager.buttons is not first_row  # 換了新的 Row
    assert pager.content.controls[1] is pager.buttons  # 並且真的掛回版面
    assert len(pager.buttons.controls) == 3  # ‹ 1 ›
    pager.set_state(2400, 5)
    assert (
        pager.content.controls[1] is pager.buttons and len(pager.buttons.controls) > 3
    )


def test_entries_list_scrolls_back_to_top_when_content_changes(db_path):
    from app.views.moddb import entries_panel

    seed(db_path)
    db = TranslationDB(db_path)
    scheduled: list = []
    page = mock_page()
    page.run_task = lambda fn, *a, **k: scheduled.append(fn)
    panel = entries_panel.EntriesPanel(page, lambda: db)
    panel.refresh()
    assert scheduled, "換清單內容時要排程把清單捲回頂端"
    offsets: list = []

    async def fake_scroll(**kwargs):
        offsets.append(kwargs)

    panel.list_view.scroll_to = fake_scroll
    import asyncio

    asyncio.run(scheduled[-1]())
    assert offsets == [{"offset": 0, "duration": 0}]
    db.close()


def _plan(db, sql, params):
    return " | ".join(r[3] for r in db._q("EXPLAIN QUERY PLAN " + sql, params))


def test_content_lookups_use_the_content_index_even_without_statistics(db_path):
    """沒有 ANALYZE 統計時，規劃器會只用 kind 掃描整張表（百萬筆時每次 150～170 毫秒）。

    條目詳情的「同鍵值／同內容」查詢與每次編輯都會跑的 preview_manual 必須用內容索引。
    """
    import inspect

    seed(db_path)
    db = TranslationDB(db_path)
    row = db.list_entries("1.21.1")[0][0]
    for method in (db.entry_detail, db._same_content):
        assert "INDEXED BY idx_entry_content" in inspect.getsource(method)
    plan = _plan(
        db,
        "SELECT e.id FROM entry e INDEXED BY idx_entry_content "
        "WHERE e.kind=? AND e.mod_id=? AND e.key=? AND e.id<>?",
        [row.kind, row.mod_id, row.key, row.id],
    )
    assert "idx_entry_content (kind=? AND mod_id=? AND key=?)" in plan

    # 實際跑一遍：結果仍正確（同內容的其他版本、同鍵值的其他版本）
    detail = db.entry_detail(row.id)
    assert [r.mc_version for r in detail.same_key] == ["1.20.1"]
    assert detail.versions == ["1.20.1", "1.21.1"]
    preview = db.preview_manual(row.id, "新譯文")
    assert {i.mc_version for i in preview} == {"1.21.1", "1.20.1"}
    db.close()


def test_versions_and_mods_are_cached_and_follow_data_changes(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    calls = []
    real_v, real_m = db._versions_uncached, db._mods_uncached
    db._versions_uncached = lambda: calls.append("v") or real_v()
    db._mods_uncached = lambda v: calls.append("m") or real_m(v)
    assert db.versions() == db.versions() == ["1.21.1", "1.20.1"]
    assert db.mods("1.21.1") == db.mods("1.21.1") == ["bar", "foo"]
    assert calls == ["v", "m"]  # 第二次都讀快取
    db.ingest("26.1", [ScanItem(KIND_LANG, "zed", "item.zed.a", "Zed")])
    assert "26.1" in db.versions() and db.mods("26.1") == ["zed"]  # 寫入後快取失效
    db.close()


def test_changed_filter_skips_the_scan_when_there_are_no_source_changes(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    queried = []
    real_q = db._q

    def spy(sql, params=()):
        queried.append(sql)
        return real_q(sql, params)

    db._q = spy
    assert db.list_entries("1.21.1", state="changed") == ([], 0)
    assert not any("FROM entry e LEFT JOIN effective" in q for q in queried)
    db.close()


def test_aborted_run_ends_session_as_error(db_path, monkeypatch):
    """P2-5：ABORTED 不可記成 DONE。"""
    seed(db_path)
    monkeypatch.setattr(moddb_translate_service, "ABORT_AFTER_FAILURES", 1)
    monkeypatch.setattr(
        moddb_translate_service,
        "translate_batch_smart",
        lambda batch, total=None, dry_run=False: (
            [{**it, "_untranslated": True} for it in batch],
            "DONE",
        ),
    )
    monkeypatch.setattr(
        moddb_translate_service,
        "run_translator_skeleton",
        _skeleton_one_per_batch(moddb_translate_service.run_translator_skeleton),
    )
    snap = run(TranslateOptions(version="1.21.1", mod_ids=("foo",)))
    assert snap["status"] == "ERROR" and snap["summary"]["status"] == "ABORTED"


def test_db_flush_failure_retries_and_keeps_buffer(db_path, monkeypatch):
    """P1-B：寫入先失敗後成功 → 不遺失譯文；一直失敗 → FAILED/ERROR 且 written 不灌水。"""
    seed(db_path)
    fake_engine(monkeypatch, lambda t: "譯:" + t)
    real = TranslationDB.write_back
    state = {"fail": 1}

    def flaky(self, *a, **k):
        if state["fail"] > 0:
            state["fail"] -= 1
            raise OSError("disk full")
        return real(self, *a, **k)

    monkeypatch.setattr(TranslationDB, "write_back", flaky)
    snap = run(TranslateOptions(version="1.21.1", mod_ids=("foo",)))
    assert snap["status"] == "DONE" and snap["summary"]["written"] == 2

    seed_db = TranslationDB(db_path)
    assert seed_db.count_untranslated("1.21.1", ["foo"]) == 0
    seed_db.close()


def test_db_flush_permanent_failure_is_error(db_path, monkeypatch):
    seed(db_path)
    fake_engine(monkeypatch, lambda t: "譯:" + t)

    def boom(self, *a, **k):
        raise OSError("locked")

    monkeypatch.setattr(TranslationDB, "write_back", boom)
    snap = run(TranslateOptions(version="1.21.1", mod_ids=("foo",)))
    assert snap["status"] == "ERROR"
    assert snap["summary"]["status"] == "FAILED" and snap["summary"]["written"] == 0


def test_reuse_skips_conflicting_other_versions(db_path):
    """P2-4：其他版本譯文不一致時不自動沿用。"""
    db = TranslationDB(db_path)
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "m", "k", "Gear")])
    db.ingest("1.20.1", [ScanItem(KIND_LANG, "m", "k", "Gear", "齒輪")])
    db.ingest("1.19.2", [ScanItem(KIND_LANG, "m", "k", "Gear", "傳動輪")])
    assert db.count_reusable("1.21.1") == 0
    assert db.reuse_from_other_versions("1.21.1") == 0
    db.ingest("1.18.2", [ScanItem(KIND_LANG, "n", "k", "Gear")])
    db.ingest("1.17.1", [ScanItem(KIND_LANG, "n", "k", "Gear", "齒輪")])
    db.ingest("1.16.5", [ScanItem(KIND_LANG, "n", "k", "Gear", "齒輪")])
    assert db.reuse_from_other_versions("1.18.2") == 1
    db.close()


def test_loop_batches_are_homogeneous_cache_type(monkeypatch):
    """P1-A：lang 與 patchouli 不可混在同一批。"""
    from translation_tool.core import lm_translator_shared_loop as loop

    seen = []
    saved = []

    def tb(batch, total=None, dry_run=False):
        seen.append({i["cache_type"] for i in batch})
        return [{**i, "text": "譯"} for i in batch], "DONE"

    items = [
        {
            "file": "m/a",
            "path": f"k{i}",
            "text": "x",
            "source_text": "x",
            "cache_type": "lang" if i < 3 else "patchouli",
        }
        for i in range(6)
    ]
    loop.translate_items_with_cache_loop(
        items,
        total_for_smart=6,
        translate_batch_smart=tb,
        write_new_cache=True,
        reload_cache=False,
        cache_add=lambda *a, **k: True,
        cache_save=lambda ct, **k: saved.append(ct) or True,
    )
    assert all(len(s) == 1 for s in seen)
    assert set(saved) == {"lang", "patchouli"}


def test_db_stop_then_final_flush_recovers_is_still_failed(db_path, monkeypatch):
    """前 3 次寫入失敗、最後補寫成功：已翻批次保存，但後續批次未執行 → FAILED/ERROR。"""
    seed(db_path)
    fake_engine(monkeypatch, lambda t: "譯:" + t)
    monkeypatch.setattr(
        moddb_translate_service,
        "run_translator_skeleton",
        _skeleton_one_per_batch(moddb_translate_service.run_translator_skeleton),
    )
    real = TranslationDB.write_back
    state = {"fail": 3}

    def flaky(self, *a, **k):
        if state["fail"] > 0:
            state["fail"] -= 1
            raise OSError("locked")
        return real(self, *a, **k)

    monkeypatch.setattr(TranslationDB, "write_back", flaky)
    snap = run(TranslateOptions(version="1.21.1", mod_ids=("foo",)))
    assert snap["status"] == "ERROR"
    assert snap["summary"]["status"] == "FAILED"
    assert snap["summary"]["written"] == 1 and snap["summary"]["remaining"] == 1


def test_reuse_picks_highest_priority_source_not_min_code(db_path):
    """同譯文、不同來源：沿用時取優先序最高者（人工），不是代碼最小者（AI）。"""
    from translation_tool.translation_db.schema import SRC_MANUAL

    db = TranslationDB(db_path)
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "m", "k", "Gear")])
    db.ingest(
        "1.20.1", [ScanItem(KIND_LANG, "m", "k", "Gear", "齒輪", source=SRC_MANUAL)]
    )
    db.ingest("1.19.2", [ScanItem(KIND_LANG, "m", "k", "Gear", "齒輪", source=SRC_AI)])
    assert db.reuse_from_other_versions("1.21.1") == 1
    row = db._q(
        "SELECT t.source FROM translation t JOIN entry e ON e.id=t.entry_id "
        "WHERE e.mc_version='1.21.1'"
    )
    assert row == [(SRC_MANUAL,)]
    db.close()


def test_list_entries_filters_by_kind_and_kinds_come_from_db(db_path):
    """類型篩選：選項取自資料庫實際出現的類型，新增類型不必改程式。"""
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "m", "a", "A"),
            ScanItem(KIND_PATCHOULI, "m", "book.page", "Page"),
            ScanItem("future_kind", "m", "x", "X"),
        ],
    )
    assert db.kinds("1.21.1") == ["future_kind", "lang", "patchouli"]
    assert db.list_entries("1.21.1", kind=KIND_PATCHOULI)[1] == 1
    assert db.list_entries("1.21.1", kind="future_kind")[1] == 1
    assert db.list_entries("1.21.1")[1] == 3
    from app.views.moddb.formatting import kind_label

    assert kind_label("future_kind") == "future_kind"  # 沒登錄名稱時顯示代碼
    db.close()


def test_tick_live_updates_elapsed_and_eta_between_batches():
    """每批結束才有新資料，但畫面要每次輪詢都更新：已用時間往上、預估剩餘往下，預計完成時刻不變。"""
    from translation_tool.translation_db.run_progress import (
        RunProgress,
        format_live,
        tick_live,
    )

    progress = RunProgress(total=100, planned_batches=4, started=1000.0)
    progress.update(25)
    live = progress.live(now=1050.0)  # 已用 50 秒、已處理 25% → 預估剩餘 150 秒
    assert live["elapsed_sec"] == 50.0 and live["eta_sec"] == 150.0

    later = tick_live(live, now=1062.0)  # 12 秒後（還沒完成下一批）
    assert later["elapsed_sec"] == 62.0 and later["eta_sec"] == 138.0
    assert later["finish_ts"] == live["finish_ts"]
    assert "已用 1:02" in format_live(later) and "預估剩餘 2:18" in format_live(later)
    # 超過預估後不會變負數
    assert tick_live(live, now=1500.0)["eta_sec"] == 0.0
    # 還沒有任何一批完成：沒有預估剩餘，但已用時間照樣跳動
    start = RunProgress(total=100, planned_batches=4, started=1000.0).live(now=1000.0)
    ticked = tick_live(start, now=1007.0)
    assert ticked["elapsed_sec"] == 7.0 and ticked["eta_sec"] is None
    # 舊格式（沒有時間戳）原樣回傳
    old = {k: v for k, v in live.items() if k not in ("started_ts", "updated_ts")}
    assert tick_live(old, now=2000.0) == old


def test_service_publishes_live_progress_before_the_first_batch(db_path, monkeypatch):
    """第一批結束前就有即時資料（已用時間從開始就會跳動）。"""
    seed(db_path)
    seen = []

    def spy(batch, total=None, dry_run=False):
        # 此時第一批尚未結束，summary 應該已含 live
        seen.append(dict(session_ref[0].snapshot().get("summary") or {}))
        return [{**it, "text": "譯:" + it["source_text"]} for it in batch], "DONE"

    session_ref: list = []
    session = TaskSession()
    session_ref.append(session)
    monkeypatch.setattr(moddb_translate_service, "translate_batch_smart", spy)
    run_moddb_translate_service(
        TranslateOptions(version="1.21.1", mod_ids=("foo",)), session
    )
    live = seen[0].get("live")
    assert live and live["batch_done"] == 0 and live["started_ts"] <= live["updated_ts"]


def test_live_timing_uses_the_monotonic_clock(monkeypatch):
    """已用時間／預估剩餘用單調時鐘：任務中系統時間被大幅調整也不會跳動。"""
    from translation_tool.translation_db import run_progress as rp

    wall = {"t": 1_000_000.0}
    mono = {"t": 50.0}
    monkeypatch.setattr(rp.time, "time", lambda: wall["t"])
    monkeypatch.setattr(rp.time, "monotonic", lambda: mono["t"])

    progress = rp.RunProgress(total=100, planned_batches=4)
    mono["t"] += 40.0
    progress.update(25)
    live = progress.live()
    assert live["elapsed_sec"] == 40.0 and live["eta_sec"] == 120.0

    mono["t"] += 10.0
    wall["t"] -= 3600.0  # 系統時間被往回調 1 小時
    ticked = rp.tick_live(live)
    assert ticked["elapsed_sec"] == 50.0 and ticked["eta_sec"] == 110.0
    assert (
        ticked["finish_ts"] == live["finish_ts"]
    )  # 預計完成時刻仍是上次估計的牆上時間
    assert progress.elapsed() == 50.0


def test_list_entries_can_filter_by_entry_ids(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    rows = db.list_entries("1.21.1")[0]
    wanted = [rows[0].id, rows[2].id]
    got, total = db.list_entries("1.21.1", entry_ids=wanted)
    assert total == 2 and {r.id for r in got} == set(wanted)
    assert db.list_entries("1.21.1", entry_ids=[])[1] == 0
    # 很多 id 也不會超過 SQLite 的參數上限
    assert db.list_entries("1.21.1", entry_ids=list(range(1, 50_000)))[1] == len(rows)
    db.close()


def test_flagged_entries_flow_from_translation_to_entries_review(db_path, monkeypatch):
    """機翻特殊字元不一致 → 統計卡「檢視」→ 條目校對只列這些條目、預填 AI 譯文並提示不一致。"""
    from app.views import moddb_view

    seed(db_path)
    # 「Infused Alloy」的譯文多帶了 §a（與原文特殊字元不一致 → 不寫入）
    fake_engine(
        monkeypatch,
        lambda t: "§a注入合金" if t == "Infused Alloy" else "譯:" + t,
    )
    summary = run(TranslateOptions(version="1.21.1", mod_ids=("foo",)))["summary"]
    assert summary["flagged"] == 1
    flagged = summary["flagged_entries"]
    assert list(flagged.values()) == ["§a注入合金"]

    view = moddb_view.ModDbView(mock_page(), mock_filepicker())
    panel = view.translate
    panel._run_version = "1.21.1"
    assert panel.view_flagged_btn.visible is False
    panel._apply_summary(summary)
    assert panel.view_flagged_btn.visible is True
    panel._view_flagged()  # 按「檢視」

    entries = view.entries
    assert view.tab == "entries" and entries.state == "none"
    assert [r.key for r in entries.rows] == ["item.foo.b"]
    assert entries.flagged_banner.visible is True
    assert entries.tw_field.value == "§a注入合金"  # 預填 AI 譯文
    assert "特殊字元不一致" in entries.token_hint.value
    assert entries.save_btn.disabled is False  # 修正後可直接儲存

    entries.clear_flagged()  # 清除篩選 → 一般清單
    assert entries.flagged_banner.visible is False and entries.entry_ids is None
    assert len(entries.rows) > 1

    # 重新開始機翻會清掉上一次的「檢視」
    panel._reset_stats()
    assert panel.view_flagged_btn.visible is False and panel._flagged == {}
    # 從總覽跳進來套用狀態篩選時，也會清掉「特殊字元不一致」篩選
    entries.show_flagged(list(flagged), flagged, "1.21.1")
    entries.show_filter("diff")
    assert entries.entry_ids is None and entries.drafts == {}


def test_translate_panel_switches_kpis_between_normal_and_ai_repair_modes():
    panel = translate_panel.TranslatePanel(mock_page(), lambda: None)

    panel._apply_summary(
        {
            "reused": 3,
            "written": 7,
            "translated": 8,
            "flagged": 1,
            "remaining": 4,
        }
    )
    assert [card.value_text.value for card in panel._stat_cards] == ["3", "7", "1", "4"]
    assert panel._kpi_titles["second"].label_text.value == "已寫入（AI 機翻）"

    panel._repair_mode = "same_source_ai"
    panel.repair_mode_group.value = "same_source_ai"
    panel._reset_stats(mode="repair")
    assert [card.value_text.value for card in panel._stat_cards] == ["—"] * 4
    assert panel._kpi_titles["first"].label_text.value == "本次候選"
    assert panel._kpi_titles["third"].label_text.value == "重翻後仍相同"

    partial = {
        "operation": "retranslate_same_source_ai",
        "candidates": 444,
        "updated": 404,
        "unchanged": 40,
        "flagged": 2,
        "skipped_changed": 3,
        "failed": 1,
        "cache_failed": 5,
        "cache_add_failed": 2,
        "cache_save_failed": 1,
        "cache_keys_changed": 80,
        "cache_keys_saved": None,
        "cache_stats_note": "1 個 key 落盤結果未確認",
        "ai_representatives": 130,
        "ai_submitted_items": 128,
        "ai_validated_items": 120,
        "dedup_mapped_candidates": 24,
        "dedup_reused_candidates": 12,
        "processed_candidates": 400,
        "not_submitted_candidates": 10,
        "unprocessed_candidates": 44,
        "remaining": 40,
        "status": "CANCELLED",
        "last_error": "使用者取消",
    }
    panel._apply_summary(partial, final=False)
    assert [card.value_text.value for card in panel._stat_cards] == [
        "444",
        "404",
        "40",
        "—",
    ]
    details = panel.repair_summary_text.value
    assert all(
        value in details
        for value in (
            "格式檢查未通過 2",
            "資料已變動跳過 3",
            "失敗 1",
            "快取未同步事件 5",
            "CANCELLED",
            "AI 代表計劃 130",
            "實際送入引擎 128 items（不是 API/HTTP 次數）",
            "等價映射候選 24；已共用驗證結果 12",
            "來源列進度 400/444；未送出候選 10；未處理候選 44",
            "快取 key 變更 80；成功落盤 未知",
            "新增失敗來源列 2；尚未確認落盤 keys 1",
        )
    )

    panel._apply_summary(partial, final=True)
    assert panel.stat_remaining.value_text.value == "40"
    assert "重翻完成" not in panel.repair_summary_text.value

    panel._reset_stats(mode="normal")
    assert panel._kpi_titles["first"].label_text.value == "沿用其他版本"
    assert panel._kpi_titles["third"].label_text.value == "特殊字元不一致"


def test_translate_panel_restores_partial_and_cancelled_repair_from_task_session():
    panel = translate_panel.TranslatePanel(mock_page(), lambda: None)
    session = TaskSession()
    session.start()
    panel.session = session
    panel._running = True

    session.set_summary(
        {
            "operation": "retranslate_same_source_ai",
            "candidates": 8,
            "updated": 3,
            "unchanged": 2,
            "flagged": 1,
            "skipped_changed": 1,
            "failed": 0,
            "remaining": 5,
            "status": "DONE",
        }
    )
    panel.sync_from_session()
    assert [card.value_text.value for card in panel._stat_cards] == ["8", "3", "2", "—"]
    assert panel._running is True

    session.request_cancel()
    session.set_summary(
        {
            "operation": "retranslate_same_source_ai",
            "candidates": 8,
            "updated": 3,
            "unchanged": 2,
            "flagged": 1,
            "skipped_changed": 1,
            "failed": 0,
            "remaining": 5,
            "status": "CANCELLED",
        }
    )
    session.finish()
    panel.sync_from_session()
    assert [card.value_text.value for card in panel._stat_cards] == ["8", "3", "2", "5"]
    assert panel.status_chip.label.value == "已取消"
    assert panel._running is False


# ------------------------------------------------ 「翻譯與原文相同」（專有名詞等不需要翻譯）
def test_translation_same_as_source_still_writes_cache_and_database(
    db_path, monkeypatch, fake_cache
):
    """譯文與原文相同不是失敗：照常寫快取與資料庫、計入已翻譯，不重送 AI。"""
    seed(db_path)
    calls: list[list[str]] = []

    def identity(batch, total=None, dry_run=False):
        calls.append([it["path"] for it in batch])
        # 「Infused Alloy」原樣回傳（視為不需翻譯的專有名詞）
        return [
            {**it, "text": it["source_text"]}
            if it["source_text"] == "Infused Alloy"
            else {**it, "text": "譯:" + it["source_text"]}
            for it in batch
        ], "DONE"

    monkeypatch.setattr(moddb_translate_service, "translate_batch_smart", identity)
    summary = run(TranslateOptions(version="1.21.1", mod_ids=("foo",)))["summary"]

    assert summary["status"] == "DONE" and summary["flagged"] == 0
    assert summary["translated"] == 2 and summary["written"] == 2
    assert len(calls) == 1  # 一批就完成，沒有因為「與原文相同」而重送
    # 快取照常寫入（否則下次遇到同一文字又會送 AI）
    assert ("lang", "item.foo.b", "Infused Alloy", "Infused Alloy") in fake_cache
    # 資料庫照常寫入，而且來源是 AI 機翻
    db = TranslationDB(db_path)
    row = next(r for r in db.list_entries("1.21.1")[0] if r.key == "item.foo.b")
    assert row.zh_tw == "Infused Alloy" and row.source == SRC_AI
    db.close()


@pytest.mark.parametrize(
    ("retry_value", "expected"),
    [("壓力室", "壓力室"), ("Pressure Chamber", "Pressure Chamber")],
    ids=["updated", "still-same"],
)
def test_same_source_retry_is_finalized_before_mod_db_and_cache_writeback(
    db_path, monkeypatch, fake_cache, retry_value, expected
):
    from translation_tool.core import lm_translator_main as main

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "foo", "item.foo.pressure", "Pressure Chamber")],
    )
    db.close()

    config = {
        "lm_translator": {
            "models": {"same-source-test-model": {"enabled": True}},
            "temperature": 0.25,
            "retry_same_as_source": True,
            "token_budget_enabled": False,
            "max_output_tokens": 2048,
            "initial_batch_size_lang": 10,
            "initial_batch_size_patchouli": 10,
            "batch_shrink_factor": 0.75,
            "min_batch_size": 1,
            "lang_system_prompt": "LANG PROFILE PROMPT",
            "patchouli_system_prompt": "PATCHOULI PROFILE PROMPT",
            "rate_limit": {"sleep_seconds_between_batches": 0},
        }
    }
    monkeypatch.setattr(main, "load_config", lambda: config)
    monkeypatch.setattr(
        "translation_tool.core.lm_config_rules._get_all_keys",
        lambda: ["test-key"],
    )
    monkeypatch.setattr(main, "interruptible_sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_shared_loop.load_config",
        lambda: config,
    )
    calls = []

    def call_api(**kwargs):
        calls.append(kwargs)
        translations = {"Pressure Chamber": retry_value} if len(calls) == 2 else {}
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "value": translations.get(item["value"], item["value"]),
                    }
                    for item in kwargs["payload"]["items"]
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(main, "call_gemini_requests", call_api)
    snapshot = run(TranslateOptions(version="1.21.1", reuse_other_versions=False))

    assert snapshot["status"].upper() == "DONE"
    assert snapshot["summary"]["translated"] == 1
    assert snapshot["summary"]["written"] == 1
    assert snapshot["summary"]["flagged"] == 0
    assert len(calls) == 2
    assert [entry["value"] for entry in calls[1]["payload"]["items"]] == [
        "Pressure Chamber"
    ]
    assert [row[2:] for row in fake_cache] == [("Pressure Chamber", expected)]
    db = TranslationDB(db_path)
    row = next(r for r in db.list_entries("1.21.1")[0] if r.key == "item.foo.pressure")
    assert row.zh_tw == expected and row.source == SRC_AI
    db.close()


def _seed_same_as_source(path):
    db = TranslationDB(path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "m", "minecraft.name", "Minecraft", "Minecraft"),
            ScanItem(KIND_LANG, "m", "jei.name", "JEI", "JEI"),
            ScanItem(KIND_LANG, "m", "pressure.name", "Pressure Chamber", "壓力室"),
            ScanItem(KIND_LANG, "m", "hello.name", "Hello"),
            ScanItem(KIND_LANG, "m", "empty.name", ""),
        ],
    )
    return db


def _keys(db, **kw):
    rows, total = db.list_entries("1.21.1", limit=100, **kw)
    return sorted(r.key for r in rows), total


def test_same_as_source_filter_uses_effective_translation_and_ignores_empty(db_path):
    db = _seed_same_as_source(db_path)
    keys, total = _keys(db, state="same")
    assert (
        keys == ["jei.name", "minecraft.name"] and total == 2
    )  # 空原文、無譯文、不同的都不算
    db.close()


def test_same_as_source_is_still_translated_and_not_untranslated(db_path):
    db = _seed_same_as_source(db_path)
    assert "minecraft.name" in _keys(db, state="all")[0]  # 全部
    assert "minecraft.name" in _keys(db, state="ok")[0]  # 其他譯文
    assert "minecraft.name" not in _keys(db, state="none")[0]  # 不是未翻譯
    assert _keys(db, state="none")[0] == ["empty.name", "hello.name"]
    assert db.count_untranslated("1.21.1") == 1  # 空原文不算待機翻；Hello 才是
    db.close()


def test_same_as_source_filter_combines_with_search(db_path):
    db = _seed_same_as_source(db_path)
    assert _keys(db, state="same", query="mine") == (["minecraft.name"], 1)
    assert _keys(db, state="same", query="pressure") == ([], 0)  # 壓力室與原文不同
    db.close()


def test_same_as_source_follows_the_effective_translation_precedence(db_path):
    """比較的是「目前生效的譯文」：人工改成不同文字後，就不再算『與原文相同』。"""
    db = _seed_same_as_source(db_path)
    entry = next(
        r for r in db.list_entries("1.21.1", state="same")[0] if r.key == "jei.name"
    )
    db.save_manual(entry.id, "JEI（物品檢視器）", actor="測試", propagate=False)
    assert _keys(db, state="same")[0] == ["minecraft.name"]
    db.close()


def test_same_as_source_count_and_pagination_share_the_condition(db_path):
    db = TranslationDB(db_path)
    items = []
    for i in range(7):
        items.append(ScanItem(KIND_LANG, "m", f"same.{i:02d}", f"Name{i}", f"Name{i}"))
        items.append(ScanItem(KIND_LANG, "m", f"diff.{i:02d}", f"Thing{i}", f"東西{i}"))
        items.append(ScanItem(KIND_LANG, "m", f"none.{i:02d}", f"Todo{i}"))
    db.ingest("1.21.1", items)
    seen: list[str] = []
    for page in range(3):
        rows, total = db.list_entries("1.21.1", state="same", limit=3, offset=page * 3)
        assert total == 7  # 每一頁的總筆數都一致
        seen += [r.key for r in rows]
    assert seen == [
        f"same.{i:02d}" for i in range(7)
    ]  # 三頁合起來沒有重複或混入其他資料
    assert db.list_entries("1.21.1", state="same", limit=3, offset=9)[0] == []
    db.close()
