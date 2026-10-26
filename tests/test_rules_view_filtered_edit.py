"""替換規則頁：搜尋／篩選狀態下的刪除、新增、排序、重載與儲存。"""

import pytest

from app.views.rules.rules_actions import confirm_discard_changes, to_saved_rules
from app.views.rules_view import RulesView
from tests.conftest import mock_page


class _Event:
    def __init__(self, **attrs):
        self.control = type("C", (), attrs)()


@pytest.fixture
def make_view(monkeypatch):
    def factory(rules):
        monkeypatch.setattr(
            "app.views.rules_view.load_replace_rules",
            lambda *a, **k: [dict(r) for r in rules],
        )
        monkeypatch.setattr(
            "app.views.rules_view.launch_page_operation",
            lambda page, fn, **kw: fn() or True,
        )
        return RulesView(mock_page())

    return factory


def _visible_from(view):
    return [r.cells[1].content.value for r in view.rules_table.rows if r.visible]


def _rules(n=120):
    return [{"from": f"w{i}", "to": f"t{i}"} for i in range(n)]


def test_delete_in_filtered_view_removes_the_shown_row(make_view):
    view = make_view(_rules())
    view._do_search("w1")
    first = view.rules_table.rows[1]  # w10
    view.delete_row_clicked(_Event(data=first.data))

    assert "w10" not in _visible_from(view)
    assert all(r["from"] != "w10" for r in view.all_rules_data)
    assert len(view.search_results) == 30
    assert view.total_count_text.value == "符合 30 條（全部 119 條）"


def test_delete_last_row_of_last_page_steps_back_one_page(make_view):
    view = make_view(_rules(51))
    view.next_page(None)
    assert view.current_page == 2
    view.delete_row_clicked(_Event(data=view.rules_table.rows[0].data))
    assert view.current_page == 1
    assert len(_visible_from(view)) == 50


def test_delete_offers_undo_that_restores_original_position(make_view, monkeypatch):
    shown = {}
    monkeypatch.setattr(
        "app.views.rules_view.show_snack",
        lambda page, msg, *a, **k: shown.update(msg=msg, **k),
    )
    view = make_view(_rules(10))
    rid = view.rules_table.rows[3].data
    view.delete_row_clicked(_Event(data=rid))
    assert shown["action_label"] == "復原"

    shown["on_action"](None)

    assert [r["from"] for r in view.all_rules_data][3] == "w3"
    assert "w3" in _visible_from(view)


def test_add_in_filtered_view_clears_search_and_shows_new_row(make_view):
    view = make_view(_rules())
    view._do_search("w1")
    view.add_row_clicked(None)

    assert view.search_results is None
    assert view.search_box.value == ""
    assert view.current_page == view.total_pages == 3
    assert _visible_from(view)[-1] == ""


def test_sort_in_filtered_view_reorders_results(make_view):
    view = make_view([{"from": "bb1", "to": "x"}, {"from": "a1", "to": "x"}])
    view._do_search("1")
    view.on_sort_change(_Event(value="from_asc"))
    assert [r["from"] for r in view.search_results] == ["a1", "bb1"]
    assert _visible_from(view) == ["a1", "bb1"]


def test_reload_rebinds_filter_to_new_rule_objects(make_view):
    view = make_view(_rules(5))
    view._do_search("w")
    old = view.search_results[0]
    view._handle_reload_success([dict(r) for r in _rules(3)])

    assert all(r is not old for r in view.search_results)
    assert len(view.search_results) == 3
    # 編輯重載後的列要真的改到資料
    row = view.rules_table.rows[0]
    field = row.cells[1].content
    field.value = "changed"
    view.on_text_change(_Event(data=field.data, value="changed"))
    assert view.all_rules_data[0]["from"] == "changed"


def test_search_numbers_rows_by_position_in_full_list(make_view):
    view = make_view(_rules())
    view._do_search("w119")
    assert view.rules_table.rows[0].cells[0].content.value == "120"


def test_unsaved_flag_and_reload_confirmation(make_view, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.views.rules_view.confirm_discard_changes",
        lambda page, on_confirm: calls.append("confirm"),
    )
    monkeypatch.setattr(
        "app.views.rules_view.start_reload_thread", lambda v: calls.append("reload")
    )
    view = make_view(_rules(3))
    assert view._has_unsaved is False
    view.reload_rules_clicked(None)
    assert calls == ["reload"]

    view.add_row_clicked(None)
    assert view._has_unsaved is True
    assert view.unsaved_text.visible is True
    view.reload_rules_clicked(None)
    assert calls == ["reload", "confirm"]

    view._handle_reload_success(_rules(3))
    assert view._has_unsaved is False


def test_confirm_discard_changes_runs_callback_only_on_confirm(monkeypatch):
    shown = []
    monkeypatch.setattr(
        "app.views.rules.rules_actions.present_page_dialog",
        lambda page, dialog: shown.append(dialog),
    )
    monkeypatch.setattr(
        "app.views.rules.rules_actions.close_page_dialog", lambda page, dialog: None
    )
    ran = []
    dialog = confirm_discard_changes(object(), lambda: ran.append(1))
    cancel, confirm = dialog.actions
    cancel.on_click(None)
    assert ran == []
    confirm.on_click(None)
    assert ran == [1] and shown == [dialog]


def test_save_failure_reveals_bad_rule_even_when_filtered(make_view):
    view = make_view(_rules(120))
    view.all_rules_data[100]["from"] = "("  # 無法編譯
    view._do_search("w1")  # 搜尋結果不含第 101 條
    seen = {}
    snapshot = [dict(r) for r in view.all_rules_data]
    failure = view._validate_all(snapshot)
    assert failure[0] == 100
    view._reveal_rule(snapshot[100]["_rid"])
    view._render_current_page()

    assert view.search_results is None
    assert view.current_page == 3
    bad = next(r for r in view.rules_table.rows if r.data == snapshot[100]["_rid"])
    seen["error"] = bad.cells[1].content.error
    assert seen["error"]
    assert bad.cells[2].content.error is None  # 只有 from 出錯，to 不標紅


def test_to_saved_rules_keeps_extra_fields_but_not_rid():
    saved = to_saved_rules(
        [
            {"from": "a", "to": "b", "comment": "c", "_rid": 1},
            {"from": " ", "to": "x", "_rid": 2},
        ]
    )
    assert saved == [{"from": "a", "to": "b", "comment": "c"}]


def test_only_issues_filter_and_row_icon(make_view):
    rules = [
        {"from": "噴濺型迅捷藥水", "to": "飛濺 迅捷藥水"},
        {"from": "迅捷", "to": "加速"},
        {"from": "other", "to": "x"},
    ]
    view = make_view(rules)
    assert len(view._issues) == 1
    assert "1" in view.only_issues_box.label

    view.on_only_issues_change(_Event(value=True))
    assert _visible_from(view) == ["噴濺型迅捷藥水"]
    icon = view.rules_table.rows[0].cells[3].content.controls[0]
    assert icon.visible is True
    assert "#2" in icon.tooltip and "迅捷" in icon.tooltip

    view.on_only_issues_change(_Event(value=False))
    assert len(_visible_from(view)) == 3


def test_edit_fixing_an_issue_updates_icon_after_rediagnosis(make_view):
    view = make_view([{"from": "ab", "to": "abc"}])
    field = view.rules_table.rows[0].cells[2].content
    field.value = "xyz"
    view.on_text_change(_Event(data=field.data, value="xyz"))
    view._rediagnose_after_edit()
    assert view._issues == {}
    assert view.rules_table.rows[0].cells[3].content.controls[0].visible is False


def test_duplicate_from_marks_only_the_from_field(make_view):
    view = make_view([{"from": "a", "to": "1"}, {"from": "a", "to": "2"}])
    row = next(
        r for r in view.rules_table.rows if r.data == view.all_rules_data[1]["_rid"]
    )
    assert "重複" in row.cells[1].content.error
    assert row.cells[2].content.error is None
