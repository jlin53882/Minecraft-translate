"""Mod 資料庫批次機翻：資料庫查詢、服務層（假翻譯引擎）與頁籤。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services_impl import moddb_service, moddb_translate_service
from app.services_impl.moddb_translate_service import (
    TranslateOptions,
    build_items,
    run_moddb_translate_service,
)
from app.tasks.task_session import TaskSession
from app.views.moddb import translate_panel
from tests.conftest import mock_filepicker, mock_page
from translation_tool.translation_db import (
    KIND_LANG,
    DbSettings,
    ScanItem,
    TranslationDB,
)
from translation_tool.translation_db.models import WriteBackItem
from translation_tool.translation_db.schema import SRC_AI, SRC_JAR_TW


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
        translate_panel,
        "threading",
        SimpleNamespace(
            Thread=lambda target=None, args=(), daemon=None: SimpleNamespace(
                start=lambda: target(*args)
            )
        ),
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


def test_list_entries_filters_by_source_even_when_not_effective(db_path):
    """來源篩選看「有沒有該來源的譯文」，不論最後採用哪個（新匯入但被蓋過的也找得到）。"""
    from translation_tool.translation_db.schema import SRC_MANUAL, SRC_SUBTITLE

    seed(db_path)
    db = TranslationDB(db_path)
    rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
    eid = rows["item.foo.a"].id
    with db._tx() as conn:  # 町宮字幕組也有一筆，但被較高優先序的人工蓋過
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw) VALUES (?,?,?)",
            (eid, SRC_SUBTITLE, "鋼鐵外殼"),
        )
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw) VALUES (?,?,?)",
            (eid, SRC_MANUAL, "鋼製外殼（人工）"),
        )
        db._refresh(conn, [eid])
    keys = lambda src: [r.key for r in db.list_entries("1.21.1", source=src)[0]]
    assert keys(SRC_SUBTITLE) == ["item.foo.a"]
    assert keys(SRC_MANUAL) == ["item.foo.a"]
    assert keys(SRC_AI) == []
    assert len(keys(None)) == 4
    db.close()


def test_entries_panel_source_filter(db_path):
    from app.views.moddb import entries_panel
    from translation_tool.translation_db.schema import SRC_JAR_TW

    seed(db_path)
    db = TranslationDB(db_path)
    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
    panel.refresh()
    assert panel.total == 4
    panel.source_filter.dropdown.value = str(SRC_JAR_TW)
    panel._on_source()
    assert panel.total == 1  # 只有 foo.a 有「模組自帶繁中」
    panel.source_filter.dropdown.value = "__all__"
    panel._on_source()
    assert panel.total == 4
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


def test_ok_filter_never_lists_untranslated_entries(db_path):
    """「有譯文」篩選不會出現（未翻譯）的條目；切換篩選後清單項目的 key 全新。"""
    from app.views.moddb import entries_panel

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


def test_config_db_path_field_cleans_quotes_and_checks_existence(db_path):
    """設定頁路徑欄位：貼上帶引號的路徑自動去引號，並即時顯示檔案是否存在。"""
    from app.ui import kit
    from app.views.config.db_location import DbLocationBanner, attach_path_hooks
    from tests.test_moddb_view import texts_of

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


def test_moddb_view_did_mount_resumes_pollers(db_path, monkeypatch):
    from app.views import moddb_view

    seed(db_path)
    view = moddb_view.ModDbView(mock_page(), mock_filepicker())
    resumed: list[str] = []
    monkeypatch.setattr(view.scan, "resume", lambda: resumed.append("scan"))
    monkeypatch.setattr(view.translate, "resume", lambda: resumed.append("translate"))
    view.did_mount()
    assert resumed == ["scan", "translate"]


@pytest.fixture
def clean_custom_sources():
    """自訂來源會就地加進全域 SOURCE_NAMES；測試結束後移除，避免影響其他測試。"""
    from translation_tool.translation_db.schema import SOURCE_NAMES

    yield
    for code in [c for c in SOURCE_NAMES if c >= 100]:
        del SOURCE_NAMES[code]


def _cfg(path, lines):
    return {"translation_db": {"path": str(path), "priority": lines}}


def test_new_names_in_priority_become_custom_sources(db_path, clean_custom_sources):
    """在「來源優先順序」輸入新名稱 → 登錄為自訂來源（代碼 100 起），並出現在各處選單。"""
    from translation_tool.translation_db.schema import SOURCE_NAMES
    from translation_tool.translation_db.settings import load_db_settings

    seed(db_path)
    lines = ["人工", "測試", "町宮字幕組", "自訂補充", "模組自帶繁中", "i18n 轉換"]
    settings = load_db_settings(_cfg(db_path, lines))
    assert SOURCE_NAMES[100] == "測試"
    assert settings.priority[:3] == (6, 100, 3)  # 人工、測試、町宮字幕組
    assert set(settings.priority) >= {0, 1, 2, 3, 4, 5, 6, 100}  # 沒列出的仍補在後面

    # 代碼穩定：重讀不變；再新增一個拿到下一個代碼；從設定移除也不會消失或重用代碼
    load_db_settings(_cfg(db_path, [*lines, "新來源B"]))
    assert SOURCE_NAMES[101] == "新來源B" and SOURCE_NAMES[100] == "測試"
    removed = load_db_settings(_cfg(db_path, ["人工"]))
    assert 100 in removed.priority and 101 in removed.priority
    assert removed.priority[-2:] == (100, 101)  # 沒列出的自訂來源依代碼接在最後


def test_typo_in_builtin_name_creates_a_separate_custom_source(
    db_path, clean_custom_sources, monkeypatch
):
    from translation_tool.translation_db.schema import SOURCE_NAMES
    from translation_tool.translation_db.settings import (
        load_db_settings,
        preview_new_source_names,
    )

    seed(db_path)
    monkeypatch.setattr(
        "translation_tool.translation_db.settings.load_db_settings",
        lambda *a, **k: DbSettings(path=str(db_path)),
    )
    load_db_settings(_cfg(db_path, ["釘宮翻譯組", "町宮字幕組"]))
    assert SOURCE_NAMES[100] == "釘宮翻譯組" and SOURCE_NAMES[3] == "町宮字幕組"
    # 設定頁即時提示：哪些是已存在的、哪些會被新增
    known, new = preview_new_source_names("人工\n町宮字幕組\n釘宮翻譯組\n另一個")
    assert known == ["人工", "町宮字幕組", "釘宮翻譯組"] and new == ["另一個"]


def test_custom_source_works_end_to_end(db_path, clean_custom_sources, monkeypatch):
    """自訂來源可寫入譯文、被篩選、顯示名稱，並算進進度條；ZIP 匯入選單也找得到。"""
    from app.views.moddb import entries_panel, scan_panel
    from app.views.moddb.formatting import source_label
    from translation_tool.translation_db.settings import load_db_settings

    seed(db_path)
    cfg = _cfg(db_path, ["人工", "測試"])
    monkeypatch.setattr(
        moddb_service, "load_db_settings", lambda: load_db_settings(cfg)
    )
    code = load_db_settings(cfg).priority[1]
    assert code == 100 and source_label(code) == "測試"

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
    assert read_custom_sources(path) == {"測試": 100} and SOURCE_NAMES[100] == "測試"
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
    field.value = "人工\n測試\n釘宮翻譯組"
    field.on_change(SimpleNamespace(control=field, data=field.value))
    assert "將新增自訂來源：測試、釘宮翻譯組" in field.helper and "說明" in field.helper
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
