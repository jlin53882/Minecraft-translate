"""批量審核（版本 + 模組 + 目前生效來源）：選取範圍、同步其他版本、CAS 與還原。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import KIND_LANG, ScanItem, TranslationDB
from translation_tool.translation_db.schema import SRC_AI, SRC_JAR_TW, SRC_MANUAL


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "bulk.db")
    yield d
    d.close()


def _item(key, en, tw, mod="foo"):
    return ScanItem(KIND_LANG, mod, key, en, tw, "")


def _entry(db, version, key):
    return next(row for row in db.list_entries(version)[0] if row.key == key)


def _manual(db, version, key):
    detail = db.entry_detail(_entry(db, version, key).id)
    return next((t for t in detail.translations if t.source == SRC_MANUAL), None)


def _reviewed(db, version, key):
    """審核不改變生效來源：回傳生效列（來源、譯文、審核者、審核狀態）。"""
    return _entry(db, version, key)


def _seed(db):
    db.ingest(
        "1.21.1",
        [
            _item("ok", "Steel", "鋼"),
            _item("bad", "Hello %s", "你好"),
            _item("done", "Iron", "鐵"),
            _item("empty", "Gold", ""),
        ],
    )
    done = _entry(db, "1.21.1", "done")
    db.review_manual(done.id, expected_zh_tw="鐵", propagate=False)


def test_candidates_follow_version_source_and_quality(db):
    _seed(db)

    passing = db.preview_bulk_review("1.21.1", SRC_JAR_TW, quality_pass_only=True)
    assert {c.key for c in passing.candidates} == {"ok"}

    everything = db.preview_bulk_review("1.21.1", SRC_JAR_TW, quality_pass_only=False)
    assert {c.key for c in everything.candidates} == {"ok", "bad"}

    # 已審核的條目不會出現（來源仍是模組自帶）；來源 6 只列人工未審核者
    assert "done" not in {c.key for c in everything.candidates}
    manual = db.preview_bulk_review("1.21.1", SRC_MANUAL, quality_pass_only=False)
    assert manual.candidates == ()
    db.save_manual(_entry(db, "1.21.1", "ok").id, "鋼材", propagate=False)
    manual = db.preview_bulk_review("1.21.1", SRC_MANUAL, quality_pass_only=False)
    assert {c.key for c in manual.candidates} == {"ok"}


def test_candidates_and_execution_follow_module_scope(db):
    """驗證此測試案例的預期結果與相容性。"""
    db.ingest(
        "1.21.1",
        [
            _item("foo-key", "Foo", "甲", mod="foo"),
            _item("bar-key", "Bar", "乙", mod="bar"),
        ],
    )

    plan = db.preview_bulk_review(
        "1.21.1", SRC_JAR_TW, mod_id="foo", quality_pass_only=False
    )

    assert plan.mod_id == "foo"
    assert [(candidate.mod_id, candidate.key) for candidate in plan.candidates] == [
        ("foo", "foo-key")
    ]

    result = db.execute_bulk_review(plan, actor="使用者")

    assert result.reviewed == 1
    assert _entry(db, "1.21.1", "foo-key").review_status == "reviewed"
    assert _entry(db, "1.21.1", "bar-key").review_status == "unreviewed"


def test_bulk_review_scope_options_only_show_modules_and_sources_needing_review(db):
    """範圍選單只列有未審核候選的模組，來源依模組分開統計。"""
    db.ingest(
        "1.21.1",
        [
            _item("foo-jar", "Foo Jar", "甲", mod="foo"),
            ScanItem(
                KIND_LANG,
                "foo",
                "foo-ai",
                "Foo AI",
                "乙",
                source=SRC_AI,
            ),
            _item("done", "Done", "已完成", mod="done"),
        ],
    )
    done = _entry(db, "1.21.1", "done")
    db.review_manual(done.id, expected_zh_tw="已完成", propagate=False)

    modules, sources_by_mod = db.bulk_review_scope_options(
        "1.21.1", quality_pass_only=False
    )

    assert modules == ("foo",)
    assert sources_by_mod[None] == (SRC_AI, SRC_JAR_TW)
    assert sources_by_mod["foo"] == (SRC_AI, SRC_JAR_TW)
    assert "done" not in sources_by_mod


def test_bulk_review_scope_options_use_projection_without_building_candidates(
    db, monkeypatch
):
    """範圍選單查詢只取模組與來源欄位，不建立完整候選物件。"""
    db.ingest("1.21.1", [_item("k", "Steel", "鋼", mod="accessories")])

    def unexpected_candidate_build(*_args, **_kwargs):
        pytest.fail("範圍選單不應建立完整批量審核候選")

    monkeypatch.setattr(db, "_bulk_review_candidates", unexpected_candidate_build)
    modules, sources_by_mod = db.bulk_review_scope_options(
        "1.21.1", quality_pass_only=False
    )

    assert modules == ("accessories",)
    assert sources_by_mod["accessories"] == (SRC_JAR_TW,)


def test_candidates_and_execution_allow_all_effective_sources(db):
    """驗證此測試案例的預期結果與相容性。"""
    db.ingest(
        "1.21.1",
        [
            _item("jar-key", "Jar", "甲"),
            ScanItem(KIND_LANG, "foo", "ai-key", "AI", "乙", source=SRC_AI),
        ],
    )

    plan = db.preview_bulk_review("1.21.1", None, mod_id="foo", quality_pass_only=False)

    assert plan.source is None
    assert {candidate.key for candidate in plan.candidates} == {"jar-key", "ai-key"}

    result = db.execute_bulk_review(plan, actor="使用者")

    assert result.reviewed == 2
    assert all(
        _entry(db, "1.21.1", key).review_status == "reviewed"
        for key in ("jar-key", "ai-key")
    )


def test_execute_reviews_selected_entries_and_honours_exclusions(db):
    db.ingest(
        "1.21.1",
        [_item("a", "A", "甲"), _item("b", "B", "乙"), _item("c", "C", "丙")],
    )
    plan = db.preview_bulk_review("1.21.1", SRC_JAR_TW)
    excluded = _entry(db, "1.21.1", "b").id

    result = db.execute_bulk_review(plan, excluded_ids=[excluded], actor="使用者")

    assert (result.reviewed, result.synced, result.stale_skipped) == (2, 0, 0)
    for key in ("a", "c"):
        row = _reviewed(db, "1.21.1", key)
        assert row.review_status == "reviewed" and row.source == SRC_JAR_TW
    assert _entry(db, "1.21.1", "b").review_status == "unreviewed"
    assert _manual(db, "1.21.1", "a") is None  # 不新增人工列


def test_sync_replaces_other_versions_and_is_reverted_as_one_batch(db):
    db.ingest("1.21.1", [_item("k", "Steel", "鋼")])
    db.ingest("1.20.1", [_item("k", "Steel", "鋼鐵")])
    db.ingest("1.19.2", [_item("k", "Steel", "鋼")])

    off = db.preview_bulk_review("1.21.1", SRC_JAR_TW, propagate=False)
    assert off.sibling_totals() == (0, 0)

    plan = db.preview_bulk_review("1.21.1", SRC_JAR_TW, propagate=True)
    assert plan.sibling_totals() == (2, 1)
    assert plan.sibling_totals(excluded=[_entry(db, "1.21.1", "k").id]) == (0, 0)

    result = db.execute_bulk_review(plan, actor="使用者")
    assert (result.reviewed, result.synced) == (1, 2)
    for version in ("1.21.1", "1.20.1", "1.19.2"):
        row = _reviewed(db, version, "k")
        assert row.zh_tw == "鋼" and row.review_status == "reviewed"
        assert row.source == SRC_JAR_TW

    assert db.revert_review_batch(result.batch) == 3
    assert _entry(db, "1.20.1", "k").zh_tw == "鋼鐵"
    assert _entry(db, "1.20.1", "k").review_status == "unreviewed"


def test_sync_off_leaves_other_versions_alone(db):
    db.ingest("1.21.1", [_item("k", "Steel", "鋼")])
    db.ingest("1.20.1", [_item("k", "Steel", "鋼")])

    db.execute_bulk_review(db.preview_bulk_review("1.21.1", SRC_JAR_TW))

    assert _entry(db, "1.21.1", "k").review_status == "reviewed"
    assert _entry(db, "1.20.1", "k").review_status == "unreviewed"


def test_execute_rejects_plan_when_entries_changed_after_preview(db):
    db.ingest("1.21.1", [_item("a", "A", "甲"), _item("b", "B", "乙")])
    plan = db.preview_bulk_review("1.21.1", SRC_JAR_TW)
    db.save_manual(_entry(db, "1.21.1", "a").id, "甲甲", propagate=False)

    with pytest.raises(ValueError, match="重新產生批量審核預覽"):
        db.execute_bulk_review(plan)

    assert _entry(db, "1.21.1", "b").review_status == "unreviewed"  # 整批都沒寫入


def _drain(page):
    page._run_all_tasks()
    page._tasks.clear()


def test_dialog_confirm_without_preview_explains_that_preview_is_required(
    db, monkeypatch
):
    from app.views.moddb import bulk_review_dialog as module
    from tests.conftest import mock_page

    monkeypatch.setattr(
        module,
        "launch_page_operation",
        lambda _page, target, **_kwargs: (target(), True)[1],
    )
    monkeypatch.setattr(
        module.BulkReviewDialog,
        "_post",
        lambda self, callback, *args, **_kwargs: callback(*args),
    )
    dialog = module.BulkReviewDialog(mock_page(), lambda: db, "1.21.1", None)
    shown: list[str] = []
    monkeypatch.setattr(
        module,
        "show_snack",
        lambda _page, message, *_args, **_kwargs: shown.append(message),
    )

    assert "產生預覽" in dialog.summary.value
    assert "產生預覽" in dialog.apply_btn.tooltip

    dialog._confirm()

    assert shown == [module.PREVIEW_REQUIRED_HINT]
    assert module.PREVIEW_REQUIRED_HINT in dialog.summary.value


def test_dialog_previews_pages_excludes_and_reverts(db):
    """驗證此測試案例的預期結果與相容性。"""
    from app.views.moddb.bulk_review_dialog import ALL_SOURCES, BulkReviewDialog
    from tests.conftest import mock_page

    db.ingest(
        "1.21.1",
        [_item(f"k{i:03d}", f"Text {i}", f"文字 {i}") for i in range(120)],
    )
    page = mock_page()
    page.run_thread = lambda target: target()
    done: list = []
    dialog = BulkReviewDialog(page, lambda: db, "1.21.1", done.append)
    _drain(page)

    # 預設：格式檢查通過 = 勾選、同步其他版本 = 關閉
    assert dialog.quality_check.value is True and dialog.sync_switch.value is False
    assert dialog.apply_btn.disabled is True
    assert [o.key for o in dialog.source_dd.options] == [ALL_SOURCES, str(SRC_JAR_TW)]
    assert dialog.source_dd.value == ALL_SOURCES

    dialog.source_dd.value = str(SRC_JAR_TW)
    dialog._preview()
    _drain(page)
    assert len(dialog.plan.candidates) == 120
    assert len(dialog.rows.controls) == 50  # 一頁 50 筆
    assert dialog.pager.visible and dialog.pager.total_pages == 3
    assert dialog.apply_btn.content == "確認審核 120 筆"

    dialog._on_page(3)
    assert len(dialog.rows.controls) == 20

    dialog.excluded = {dialog.plan.candidates[0].entry_id}
    dialog._render_plan()
    assert dialog.apply_btn.content == "確認審核 119 筆"

    dialog._confirm()
    _drain(page)
    assert dialog.result.reviewed == 119 and dialog.revert_btn.visible
    assert done and _entry(db, "1.21.1", "k001").review_status == "reviewed"
    assert _entry(db, "1.21.1", "k000").review_status == "unreviewed"

    dialog._revert()
    _drain(page)
    assert _entry(db, "1.21.1", "k001").review_status == "unreviewed"
    assert dialog.revert_btn.visible is False


def test_dialog_can_preview_all_sources(db):
    """驗證此測試案例的預期結果與相容性。"""
    from app.views.moddb.bulk_review_dialog import ALL_SOURCES, BulkReviewDialog
    from tests.conftest import mock_page

    db.ingest(
        "1.21.1",
        [
            _item("jar-key", "Jar", "甲"),
            ScanItem(KIND_LANG, "foo", "ai-key", "AI", "乙", source=SRC_AI),
        ],
    )
    page = mock_page()
    page.run_thread = lambda target: target()
    dialog = BulkReviewDialog(page, lambda: db, "1.21.1", None)
    _drain(page)
    dialog.quality_check.value = False
    dialog.source_dd.value = ALL_SOURCES

    dialog._preview()
    _drain(page)

    assert dialog.plan.source is None
    assert {candidate.key for candidate in dialog.plan.candidates} == {
        "jar-key",
        "ai-key",
    }


def test_dialog_option_change_invalidates_preview(db):
    """驗證篩選選項變更會清除舊預覽並停用確認操作。"""
    from app.views.moddb.bulk_review_dialog import BulkReviewDialog
    from tests.conftest import mock_page

    db.ingest("1.21.1", [_item("a", "A", "甲")])
    page = mock_page()
    page.run_thread = lambda target: target()
    dialog = BulkReviewDialog(page, lambda: db, "1.21.1", None)
    dialog.source_dd.value = str(SRC_JAR_TW)
    dialog._preview()
    _drain(page)
    assert dialog.plan is not None and dialog.apply_btn.disabled is False

    dialog.sync_switch.value = True
    dialog._invalidate()

    assert dialog.plan is None and dialog.apply_btn.disabled is True


def test_dialog_filters_preview_by_module(db, monkeypatch):
    """驗證此測試案例的預期結果與相容性。"""
    from app.views.moddb.bulk_review_dialog import ALL_SOURCES, BulkReviewDialog
    from tests.conftest import mock_page

    db.ingest(
        "1.21.1",
        [
            _item("foo-key", "Foo", "甲", mod="foo"),
            _item("bar-key", "Bar", "乙", mod="bar"),
        ],
    )
    original_scope_options = db.bulk_review_scope_options
    scope_calls = []

    def scope_options(version, **kwargs):
        scope_calls.append((version, kwargs))
        return original_scope_options(version, **kwargs)

    monkeypatch.setattr(db, "bulk_review_scope_options", scope_options)
    page = mock_page()
    page.run_thread = lambda target: target()
    dialog = BulkReviewDialog(page, lambda: db, "1.21.1", None)
    _drain(page)
    assert len(scope_calls) == 1

    assert dialog.mod_dd.value == "__all__"
    assert {option.key for option in dialog.mod_dd.options} == {"__all__", "foo", "bar"}
    dialog.mod_dd.value = "foo"
    dialog._on_mod()
    assert len(scope_calls) == 1
    assert [option.key for option in dialog.source_dd.options] == [
        ALL_SOURCES,
        str(SRC_JAR_TW),
    ]
    dialog.source_dd.value = str(SRC_JAR_TW)
    dialog._preview()
    _drain(page)

    assert dialog.plan.mod_id == "foo"
    assert {candidate.mod_id for candidate in dialog.plan.candidates} == {"foo"}


def test_dialog_refreshes_scope_options_after_apply_and_revert(db):
    """批量審核與還原後，仍開啟的模組／來源選單要立即反映資料庫。"""
    from app.views.moddb.bulk_review_dialog import BulkReviewDialog
    from tests.conftest import mock_page

    db.ingest(
        "1.21.1",
        [
            _item("foo-key", "Foo", "甲", mod="foo"),
            _item("bar-key", "Bar", "乙", mod="bar"),
        ],
    )
    page = mock_page()
    page.run_thread = lambda target: target()
    dialog = BulkReviewDialog(page, lambda: db, "1.21.1", None)
    _drain(page)

    assert {option.key for option in dialog.mod_dd.options} == {
        "__all__",
        "foo",
        "bar",
    }
    dialog.mod_dd.value = "foo"
    dialog._on_mod()
    dialog._preview()
    _drain(page)
    dialog._confirm()
    _drain(page)

    assert {option.key for option in dialog.mod_dd.options} == {"__all__", "bar"}
    assert dialog.mod_dd.value == "__all__"

    dialog._revert()
    _drain(page)

    assert {option.key for option in dialog.mod_dd.options} == {
        "__all__",
        "foo",
        "bar",
    }


def test_dialog_prefills_scope_without_creating_preview(db):
    """驗證批量審核可預填模組與來源，且開啟時仍沒有預覽。"""
    from app.views.moddb.bulk_review_dialog import ALL_SOURCES, BulkReviewDialog
    from tests.conftest import mock_page

    db.ingest("1.21.1", [_item("k", "Steel", "鋼", mod="accessories")])
    page = mock_page()
    page.run_thread = lambda target: target()
    dialog = BulkReviewDialog(
        page,
        lambda: db,
        "1.21.1",
        None,
        mod_id="accessories",
        effective_source=SRC_JAR_TW,
    )
    _drain(page)

    assert dialog.mod_dd.value == "accessories"
    assert dialog.source_dd.value == str(SRC_JAR_TW)
    assert dialog.plan is None
    assert dialog.apply_btn.disabled is True
    assert dialog.source_dd.value != ALL_SOURCES


def test_sync_rejects_plan_when_another_version_changed_after_preview(db):
    db.ingest("1.21.1", [_item("k", "Steel", "鋼")])
    db.ingest("1.20.1", [_item("k", "Steel", "鋼鐵")])
    plan = db.preview_bulk_review("1.21.1", SRC_JAR_TW, propagate=True)

    # 預覽後，其他版本被另一個操作改過譯文
    db.save_manual(_entry(db, "1.20.1", "k").id, "高級鋼材", propagate=False)

    with pytest.raises(ValueError, match="其他版本"):
        db.execute_bulk_review(plan)
    assert _manual(db, "1.20.1", "k").zh_tw == "高級鋼材"
    assert _manual(db, "1.21.1", "k") is None


def test_sync_ignores_changes_to_siblings_of_excluded_entries(db):
    db.ingest("1.21.1", [_item("a", "A", "甲"), _item("b", "B", "乙")])
    db.ingest("1.20.1", [_item("a", "A", "甲甲"), _item("b", "B", "乙乙")])
    plan = db.preview_bulk_review("1.21.1", SRC_JAR_TW, propagate=True)
    db.save_manual(_entry(db, "1.20.1", "b").id, "改過", propagate=False)

    result = db.execute_bulk_review(plan, excluded_ids=[_entry(db, "1.21.1", "b").id])

    assert (result.reviewed, result.synced) == (1, 1)
    assert _manual(db, "1.20.1", "b").zh_tw == "改過"


def test_whitespace_only_translation_is_not_a_candidate(db):
    db.ingest("1.21.1", [_item("blank", "Blank", " \n"), _item("ok", "Ok", "好")])
    plan = db.preview_bulk_review("1.21.1", SRC_JAR_TW, quality_pass_only=False)
    assert {c.key for c in plan.candidates} == {"ok"}


def test_rejects_plan_when_non_effective_manual_translation_changed(db):
    """生效來源不是人工時，預覽後人工譯文被改也要拒絕，否則會被審核覆寫。"""
    db.ingest("1.21.1", [_item("k", "Steel", "鋼")])
    db.save_manual(_entry(db, "1.21.1", "k").id, "鋼材", propagate=False)
    with db._tx() as conn:  # 讓模組自帶繁中的優先序高於人工譯文
        conn.execute("UPDATE effective SET zh_tw='鋼', source=?", (SRC_JAR_TW,))
    plan = db.preview_bulk_review("1.21.1", SRC_JAR_TW, quality_pass_only=False)
    assert [c.key for c in plan.candidates] == ["k"]

    db.save_manual(_entry(db, "1.21.1", "k").id, "高級鋼材", propagate=False)
    with db._tx() as conn:
        conn.execute("UPDATE effective SET zh_tw='鋼', source=?", (SRC_JAR_TW,))

    with pytest.raises(ValueError, match="重新產生批量審核預覽"):
        db.execute_bulk_review(plan)


def test_sync_rejects_plan_when_sibling_review_metadata_changed(db):
    db.ingest("1.21.1", [_item("k", "Steel", "鋼")])
    db.ingest("1.20.1", [_item("k", "Steel", "鋼鐵")])
    plan = db.preview_bulk_review("1.21.1", SRC_JAR_TW, propagate=True)

    with db._tx() as conn:  # 只改審核者，文字與來源不變
        conn.execute(
            "UPDATE effective SET checker='別人' WHERE entry_id=?",
            (_entry(db, "1.20.1", "k").id,),
        )

    with pytest.raises(ValueError, match="其他版本"):
        db.execute_bulk_review(plan)


def test_dialog_has_a_single_scroll_container(db):
    from app.views.moddb.bulk_review_dialog import BulkReviewDialog
    from tests.conftest import mock_page

    dialog = BulkReviewDialog(mock_page(), lambda: db, "1.21.1", None)
    assert dialog.rows.scroll is None and dialog.rows.height is None


def test_sync_preview_matches_execution_when_another_source_is_reviewed(db):
    from translation_tool.translation_db.schema import SRC_AI

    db.ingest("1.21.1", [_item("k", "Steel", "甲")])
    db.ingest(
        "1.20.1",
        [ScanItem(KIND_LANG, "foo", "k", "Steel", "乙", "", source=SRC_AI)],
    )
    # 另一版本是 AI 來源、已審核、譯文不同：實際審核會保護它
    db.review_manual(_entry(db, "1.20.1", "k").id, expected_zh_tw="乙", propagate=False)

    plan = db.preview_bulk_review("1.21.1", SRC_JAR_TW, propagate=True)
    assert plan.sibling_totals() == (0, 0)

    result = db.execute_bulk_review(plan, actor="使用者")
    assert (result.reviewed, result.synced) == (1, 0)
    assert _entry(db, "1.20.1", "k").zh_tw == "乙"
