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
                mc_version="1.20.1", source=3, zh_tw="分配間隔 [ticks]", same_text=False
            ),
            NS(mc_version="1.19.2", source=None, zh_tw="", same_text=False),
        ],
        same_text=[],
    )
    controls = build_suggestions(detail, "key", applied.append)
    buttons = apply_buttons(controls[0])
    assert len(buttons) == 1
    buttons[0].on_click(None)
    assert applied == ["分配間隔 [ticks]"]
    assert apply_buttons(controls[1]) == []  # 沒有譯文的版本不提供套用


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
    assert "全部 3 筆" in panel.count_text.value
    panel.limit_field.value = "2"
    panel._on_limit_changed()
    assert "上限 2 筆" in panel.count_text.value
    assert "最多翻譯 2 筆" in panel.count_text.value
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


def _skeleton_one_per_batch(real):
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
