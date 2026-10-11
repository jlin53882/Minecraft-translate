"""Mod DB 效能與版面調整的回歸測試：state=changed、跨版本傳播、批次預覽卡片、頁碼列。"""

from __future__ import annotations

from collections import Counter

import flet as ft
import pytest

from app.ui import kit
from app.views.moddb.batch_replace_preview import (
    SelectionBar,
    render_batch_preview_rows,
)
from app.views.moddb.format_diff_review import FormatDiffReview
from tests.test_moddb_view import texts_of
from translation_tool.translation_db import (
    KIND_LANG,
    EntryFilter,
    ScanItem,
    TranslationDB,
)
from translation_tool.translation_db.quality import (
    format_tokens,
    repair_input_issues,
    repair_input_issues_from_counts,
)


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "t.db")
    yield database
    database.close()


# ---- repair_input_issues_from_counts -------------------------------------------------

_PAIRS = [
    ("a", "b"),
    ("a\nb", "ab"),
    ("a\nb", "a b %s"),
    ("§7a %s\n", "§7甲"),
    ("$(br)x\\n", "x\n"),
    ("100%", "100%"),
    ("{0} {1}", "{0}"),
]


@pytest.mark.parametrize(("source", "previous"), _PAIRS)
def test_repair_input_issues_from_counts_matches_text_version(source, previous):
    expected = repair_input_issues(source, previous)
    got = repair_input_issues_from_counts(
        format_tokens(source), format_tokens(previous)
    )

    assert got == expected


def test_repair_input_issues_from_counts_drops_newline_from_both_counters():
    wanted, previous = Counter({"\n": 2, "%s": 1}), Counter({"\n": 1, "%s": 1})

    eligible, hard, mixed = repair_input_issues_from_counts(wanted, previous)

    assert (eligible, hard, mixed) == (True, (), False)
    assert "\n" not in wanted and "\n" not in previous


# ---- state=changed ------------------------------------------------------------------


def test_state_changed_returns_only_entries_with_source_changes(db):
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", "k.a", "Alpha", "甲"),
            ScanItem(KIND_LANG, "foo", "k.b", "Beta", "乙"),
            ScanItem(KIND_LANG, "bar", "k.c", "Gamma", "丙"),
        ],
    )
    db.ingest("1.20.1", [ScanItem(KIND_LANG, "foo", "k.a", "Alpha", "甲")])
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "foo", "k.b", "Beta v2", "乙")])
    db.ingest("1.20.1", [ScanItem(KIND_LANG, "foo", "k.a", "Alpha v2", "甲")])

    rows, total = db.list_entries("1.21.1", state="changed")
    assert (total, [(r.mod_id, r.key) for r in rows]) == (1, [("foo", "k.b")])

    # 版本條件要套到 src_change，不會混進其他版本的變動。
    rows_old, total_old = db.list_entries("1.20.1", state="changed")
    assert (total_old, [r.key for r in rows_old]) == (1, ["k.a"])

    # 與其他條件（模組）組合時仍相交。
    _rows, none_total = db.list_entries("1.21.1", mod_id="bar", state="changed")
    assert none_total == 0


# ---- 批次取代：跨版本傳播只查「真的會被取代」的根條目 ------------------------------------


def test_propagate_preview_only_expands_roots_that_contain_find_text(db):
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", "item.hit", "Steel Casing", "鋼製外殼"),
            ScanItem(KIND_LANG, "foo", "item.miss", "Plain Block", "普通方塊"),
        ],
    )
    db.ingest(
        "1.20.1",
        [
            ScanItem(KIND_LANG, "foo", "item.hit", "Steel Casing", "鋼製外殼"),
            ScanItem(KIND_LANG, "foo", "item.miss", "Plain Block", "普通方塊"),
        ],
    )

    plan = db.preview_batch_replace(
        EntryFilter(version="1.21.1"), "鋼", "钢", propagate=True
    )

    roots = [c for c in plan.changes if not c.is_extra_version]
    extras = [c for c in plan.changes if c.is_extra_version]
    assert [c.key for c in roots] == ["item.hit"]
    assert [(c.mc_version, c.key, c.new_zh_tw) for c in extras] == [
        ("1.20.1", "item.hit", "钢製外殼")
    ]
    assert extras[0].root_entry_id == roots[0].entry_id


# ---- SelectionBar ---------------------------------------------------------------------


def test_selection_bar_reflects_counts_and_disables_useless_buttons():
    calls: list[str] = []
    bar = SelectionBar(lambda: calls.append("all"), lambda: calls.append("none"))

    bar.sync(total=5, excluded=0, visible=True, busy=False)
    assert bar.control.visible is True
    assert "5 / 5" in bar.text.value
    assert bar.select_all_btn.disabled is True  # 已全選
    assert bar.clear_all_btn.disabled is False

    bar.sync(total=5, excluded=5, visible=True, busy=False)
    assert "0 / 5" in bar.text.value
    assert bar.select_all_btn.disabled is False
    assert bar.clear_all_btn.disabled is True

    bar.sync(total=5, excluded=2, visible=True, busy=True)
    assert bar.select_all_btn.disabled and bar.clear_all_btn.disabled

    bar.sync(total=0, excluded=0, visible=True, busy=False)
    assert bar.control.visible is False
    bar.sync(total=5, excluded=0, visible=False, busy=False)
    assert bar.control.visible is False

    bar.select_all_btn.on_click(None)
    bar.clear_all_btn.on_click(None)
    assert calls == ["all", "none"]


# ---- 預覽卡片 -------------------------------------------------------------------------


def _one_change(db):
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "foo", "item.a", "Steel", "地獄鋼")])
    plan = db.preview_batch_replace(EntryFilter(version="1.21.1"), "地獄", "下界")
    return plan.changes[0]


def test_preview_cards_dim_excluded_roots_and_keep_detail_text(db):
    change = _one_change(db)
    common = {
        "catalog": db.source_catalog,
        "show_skipped": False,
        "busy": False,
        "on_toggle": lambda *_: None,
        "empty_message": "空",
    }

    (kept,) = render_batch_preview_rows(
        [change], excluded_roots=set(), selection_active=True, **common
    )
    (dimmed,) = render_batch_preview_rows(
        [change], excluded_roots={change.entry_id}, selection_active=True, **common
    )

    assert kept.opacity == 1.0
    assert dimmed.opacity == 0.6
    visible = texts_of(kept)
    assert "原文：Steel" in visible
    assert "地獄鋼" in visible and "下界鋼" in visible
    assert any(t.startswith("生效來源：") for t in visible)
    assert f"1.21.1 · foo · {KIND_LANG} · item.a" in visible


def test_preview_skipped_card_keeps_reason_in_one_line():
    skipped = type(
        "Skipped",
        (),
        {"mc_version": "1.21.1", "key": "item.x", "reason": "沒有可取代的內容"},
    )()

    (card,) = render_batch_preview_rows(
        [skipped],
        catalog=None,
        excluded_roots=set(),
        selection_active=False,
        show_skipped=True,
        busy=False,
        on_toggle=lambda *_: None,
        empty_message="空",
    )

    assert texts_of(card) == ["略過・1.21.1・item.x：沒有可取代的內容"]


def test_preview_empty_rows_show_message():
    controls = render_batch_preview_rows(
        [],
        catalog=None,
        excluded_roots=set(),
        selection_active=False,
        show_skipped=False,
        busy=False,
        on_toggle=lambda *_: None,
        empty_message="沒有項目",
    )

    assert texts_of(controls[0]) == ["沒有項目"]


# ---- 格式差異檢視：編輯頁版面與檢查表 ----------------------------------------------------


def test_edit_tab_shows_source_box_and_puts_meta_chips_next_to_target_label():
    review = FormatDiffReview(context_label="t", editable_control=ft.TextField())

    column = review._edit_content()[0]
    assert isinstance(column, ft.Column)
    assert column.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
    all_text = texts_of(column)
    assert "原文 en_us" in all_text
    assert "譯文 zh_tw" in all_text
    # 原文區塊在譯文標題之前
    assert all_text.index("原文 en_us") < all_text.index("譯文 zh_tw")


def test_edit_tab_hides_source_box_when_show_source_is_false():
    review = FormatDiffReview(
        context_label="t", editable_control=ft.TextField(), show_source=False
    )

    all_text = texts_of(review._edit_content()[0])

    assert "原文 en_us" not in all_text
    assert "譯文 zh_tw" in all_text


def test_result_table_lists_counts_and_unknown_location_hint():
    from app.services_impl.moddb_format_diff_service import compare_format_diff

    review = FormatDiffReview(context_label="t", editable_control=ft.TextField())
    result = compare_format_diff("§7a§7b§7c", "甲§7乙")

    table_text = texts_of(review._result_table(result))

    assert "檢查項目" in table_text and "差異" in table_text
    assert "3" in table_text and "1" in table_text
    assert any("缺少" in t and "2" in t for t in table_text)
    assert any("位置無法可靠判定" in t for t in table_text)


# ---- Pager ----------------------------------------------------------------------------


def test_pager_window_narrows_for_very_large_page_counts():
    def numbers(pager):
        return [
            int(cell.content.value)
            for cell in pager.buttons.controls
            if isinstance(cell.content, ft.Text) and cell.content.value.isdigit()
        ]

    normal = kit.Pager(1000, page=500, page_size=1)  # 1000 頁
    huge = kit.Pager(20_000, page=10_000, page_size=1)  # 20000 頁

    assert len(numbers(normal)) <= 9
    assert len(numbers(huge)) < len(numbers(normal))
    assert 10_000 in numbers(huge)


def test_pager_ellipsis_is_plain_text_without_border():
    pager = kit.Pager(1000, page=500, page_size=1)

    ellipses = [
        cell
        for cell in pager.buttons.controls
        if isinstance(cell.content, ft.Text) and cell.content.value == "…"
    ]

    assert ellipses
    assert all(cell.border is None and not cell.expand for cell in ellipses)


# ---- 搜尋框刷新指示器佔位 ----------------------------------------------------------------


def test_entries_refresh_indicator_reserves_fixed_height(tmp_path):
    from app.views.moddb import entries_panel
    from tests.conftest import mock_page
    from tests.test_moddb_view import seed

    path = tmp_path / "e.db"
    seed(path)
    database = TranslationDB(path)
    try:
        panel = entries_panel.EntriesPanel(mock_page(), lambda: database)
        holders = [
            c
            for c in panel.controls
            if isinstance(c, ft.Container) and c.content is panel.refresh_indicator
        ]
        assert holders and holders[0].height == 18
    finally:
        database.close()


def test_dialog_select_all_and_clear_all_change_exclusions(db):
    from app.views.moddb.batch_replace_dialog import BatchReplaceDialog
    from tests.conftest import mock_page

    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", "k.a", "A", "地獄一"),
            ScanItem(KIND_LANG, "foo", "k.b", "B", "地獄二"),
        ],
    )
    plan = db.preview_batch_replace(EntryFilter(version="1.21.1"), "地獄", "下界")
    dialog = BatchReplaceDialog(
        mock_page(), lambda: db, EntryFilter(version="1.21.1"), lambda _r: None
    )
    dialog.plan = dialog.selection_plan = plan
    dialog._plan_catalog = db.source_catalog
    roots = {c.entry_id for c in plan.root_changes}
    assert len(roots) == 2

    dialog._clear_all()
    assert dialog.excluded_roots == roots
    assert dialog.plan is None  # 勾選範圍變了，必須重新預覽

    dialog.plan = plan
    dialog._select_all()
    assert dialog.excluded_roots == set()
    assert "2 / 2" in dialog.selection_bar.text.value


def test_custom_source_named_like_builtin_is_shadowed_in_choices_and_priority():
    from translation_tool.translation_db.settings import serialize_priority_lines
    from translation_tool.translation_db.source_catalog import SourceCatalog

    catalog = SourceCatalog.from_registry({"測試": 100, "釘宮翻譯組": 101})

    assert catalog.canonical_code(101) == 3
    assert catalog.canonical_code(100) == 100
    assert catalog.selectable_custom_codes == (100,)
    # 歷史登錄的 custom:101 存檔時改寫成內建來源，且不與內建重複
    assert serialize_priority_lines(
        ["custom:100", "custom:101", "builtin:subtitle"], catalog
    ) == ["custom:100", "builtin:subtitle"]


def test_edit_tab_keeps_same_container_across_rerenders_so_input_keeps_focus():
    field = ft.TextField()
    review = FormatDiffReview(context_label="t", editable_control=field)
    review._select_tab("edit")
    first = review.control.content.controls[-1]

    review.render("Use %s", "使用")
    review.render("Use %s", "使用 %")
    last = review.control.content.controls[-1]

    assert last is first
    assert field in last.controls


def _registry_db(tmp_path, registry, used_codes=()):
    import json
    import sqlite3

    path = tmp_path / "reg.db"
    database = TranslationDB(path)
    database.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "foo", f"k.{i}", f"En {i}", f"譯{i}") for i in range(3)],
    )
    database.close()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('custom_sources', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(registry, ensure_ascii=False),),
        )
        for i, code in enumerate(used_codes):
            conn.execute(
                "INSERT INTO translation (entry_id, source, zh_tw) VALUES (?,?,?)",
                (i + 1, code, "x"),
            )
    return path


def test_prune_unused_custom_sources_keeps_referenced_ones(tmp_path):
    from translation_tool.translation_db.settings import (
        prune_unused_custom_sources,
        read_custom_sources,
    )

    path = _registry_db(
        tmp_path, {"測試": 100, "釘宮翻譯組": 101, "Pack": 102}, used_codes=(102,)
    )

    removed, kept = prune_unused_custom_sources(path)

    assert sorted(removed) == ["測試", "釘宮翻譯組"]
    assert kept == ["Pack"]
    assert read_custom_sources(path) == {"Pack": 102}
    # 再跑一次沒有東西可清，且不會動到仍在使用的來源
    assert prune_unused_custom_sources(path) == ([], ["Pack"])


def test_prune_banner_button_cleans_priority_lines(tmp_path, monkeypatch):
    from app.views.config import db_location
    from translation_tool.translation_db.settings import read_custom_sources

    path = _registry_db(tmp_path, {"測試": 100, "Pack": 102}, used_codes=(102,))
    monkeypatch.setattr(
        db_location, "current_settings", lambda: type("S", (), {"path": str(path)})()
    )
    banner = db_location.DbLocationBanner()
    field = ft.TextField(value="人工\n測試\nPack\ncustom:100")
    rechecked: list[int] = []
    db_location.attach_prune_cleanup(banner, field, lambda: rechecked.append(1))

    banner._on_prune()

    assert read_custom_sources(path) == {"Pack": 102}
    assert field.value == "人工\nPack"
    assert rechecked == [1]
    assert "測試" in banner.note.value
