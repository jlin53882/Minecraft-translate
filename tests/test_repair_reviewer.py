from app.services_impl import moddb_repair_review_store as store
from app.ui.design import C
from app.views.moddb.repair_reviewer import RepairReviewer
from app.views.moddb.result_inspector import TranslationResultInspector
from tests.conftest import mock_page


def _snack_messages(page):
    import flet as ft

    return [
        control.content.value
        for control in page.overlay
        if isinstance(control, ft.SnackBar)
    ]


def test_inspector_mounts_one_item_from_full_persisted_run(tmp_path):
    path = store.create_run(tmp_path / "review.db", "run-2")
    for index in range(205):
        store.append_item(
            path,
            "run-2",
            {
                "key": f"repair.{index}",
                "version": "1.21.1",
                "mod_id": "foo",
                "kind": "lang",
                "source_id": 0,
                "en_us": "Use %s",
                "old_translation": "使用",
                "ai_translation": "Use",
                "issues": ("少了 %s",),
            },
        )
    page = mock_page()
    inspector = TranslationResultInspector(page)
    inspector.set_mode("repair", "quality_mismatch")
    inspector.set_repair_results(
        {
            "review_store_path": str(path),
            "review_run_id": "run-2",
            "reviewable_results": 205,
        }
    )

    inspector.inspect()

    dialog = page.overlay[-1]
    assert dialog.title.value == "AI 修復結果・逐筆審查"
    body = dialog.content.content
    assert len(body.controls) < 20
    assert any(getattr(control, "value", "") == "repair.0" for control in body.controls)
    assert all(
        "特殊字元差異" not in str(getattr(control, "value", ""))
        for control in body.controls
    )


def test_keep_old_uses_defined_semantic_color():
    reviewer = RepairReviewer(mock_page(), "unused.db", "run-1")
    reviewer.item = {"item_index": 1, "draft": "舊譯文"}
    reviewer.draft = __import__("flet").TextField(value="舊譯文")
    reviewer._render = lambda: None
    reviewer._save_draft = lambda **_kwargs: None

    reviewer._keep_old()

    assert reviewer.status.value == "已保留舊譯文；沒有寫入翻譯資料，也沒有新增歷史。"
    assert reviewer.status.color == C.EM


def test_navigation_reports_first_and_last_item_boundaries(tmp_path):
    path = store.create_run(tmp_path / "review-boundary.db", "run-boundary")
    store.append_item(
        path,
        "run-boundary",
        {
            "key": "repair.boundary",
            "version": "1.21.1",
            "mod_id": "foo",
            "kind": "lang",
            "source_id": 0,
            "en_us": "Use %s",
            "old_translation": "使用",
            "ai_translation": "使用 %s",
        },
    )
    page = mock_page()
    reviewer = RepairReviewer(page, str(path), "run-boundary")
    reviewer.open()

    reviewer._move(-1)
    assert _snack_messages(page)[-1] == "已經是第一筆，沒有更早的待審查項目。"

    reviewer._move(1)
    assert _snack_messages(page)[-1] == "已經是最後一筆，沒有下一筆待審查項目。"


def test_apply_with_format_issue_requires_human_confirmation(tmp_path):
    path = store.create_run(tmp_path / "review-confirm.db", "run-confirm")
    store.append_item(
        path,
        "run-confirm",
        {
            "key": "repair.format",
            "version": "1.21.1",
            "mod_id": "foo",
            "kind": "lang",
            "source_id": 0,
            "en_us": "Use %s",
            "old_translation": "使用",
            "ai_translation": "Use",
            "draft": "Use",
        },
    )
    page = mock_page()
    reviewer = RepairReviewer(page, str(path), "run-confirm")
    reviewer.open()

    reviewer._apply()

    dialog = reviewer.apply_confirmation_dialog
    assert dialog is not None
    assert dialog.open is True
    assert not page._tasks
    assert len(dialog.actions) == 2


def test_apply_last_item_reports_review_completion(tmp_path, monkeypatch):
    path = store.create_run(tmp_path / "review-complete.db", "run-complete")
    item_index = store.append_item(
        path,
        "run-complete",
        {
            "key": "repair.complete",
            "version": "1.21.1",
            "mod_id": "foo",
            "kind": "lang",
            "source_id": 0,
            "en_us": "Use %s",
            "old_translation": "使用",
            "ai_translation": "使用 %s",
            "draft": "使用 %s",
        },
    )

    def apply_and_save(_database_path, store_path, run_id, item, draft):
        store.save_review_state(
            store_path, run_id, item["item_index"], "applied", draft
        )
        return "applied"

    monkeypatch.setattr(
        "app.views.moddb.repair_reviewer.apply_and_record_repair_review",
        apply_and_save,
    )
    page = mock_page()
    reviewer = RepairReviewer(page, str(path), "run-complete")
    reviewer.open()

    import asyncio

    asyncio.run(reviewer._apply_async(dict(reviewer.item), "使用 %s"))

    assert (
        store.load_item(path, "run-complete", item_index)["review_status"] == "applied"
    )
    assert "已套用最後一筆待確認內容" in _snack_messages(page)[-1]


def test_draft_box_shows_live_format_check_but_allows_human_confirmed_apply(tmp_path):
    path = store.create_run(tmp_path / "review.db", "run-live-check")
    item_index = store.append_item(
        path,
        "run-live-check",
        {
            "key": "repair.placeholder",
            "version": "1.21.1",
            "mod_id": "foo",
            "kind": "lang",
            "source_id": 0,
            "en_us": "Use %s",
            "old_translation": "舊譯文 %s",
            "ai_translation": "草稿",
            "draft": "草稿",
            "issues": ("少了 1 個「%s」",),
        },
    )
    page = mock_page()
    reviewer = RepairReviewer(page, str(path), "run-live-check")
    reviewer.open()

    assert reviewer.draft.height == 360
    assert reviewer.draft.width == 524
    assert reviewer.apply_button.disabled is False
    assert "格式檢查：少了 1 個「%s」" == reviewer.format_status.value
    assert reviewer.format_diff_viewer.summary.value == (
        "缺少 %s × 1（位置無法可靠判定）"
    )
    assert all(
        "格式差異" not in str(getattr(control, "value", ""))
        for control in reviewer.body.controls
    )

    reviewer.draft.value = "翻譯文字 %s，保留由人工確認的內容"
    reviewer._draft_changed()

    assert reviewer.apply_button.disabled is False
    assert reviewer.format_status.value == "格式檢查通過。"
    assert reviewer.format_diff_viewer.summary.value == "格式檢查通過。"
    saved = store.load_item(path, "run-live-check", item_index)
    assert saved["draft"] == "翻譯文字 %s，保留由人工確認的內容"
    assert "缺少" not in saved["draft"]

    reviewer._apply()

    assert page._tasks
    assert reviewer.status.value == "正在重新驗證來源與 revision…"
    assert reviewer.apply_button.disabled is True
    assert reviewer.close_button.disabled is True


def test_close_preserves_applied_status_and_reports_current_counts(tmp_path):
    path = store.create_run(tmp_path / "review.db", "run-applied")
    item_index = store.append_item(
        path,
        "run-applied",
        {
            "key": "repair.applied",
            "version": "1.21.1",
            "mod_id": "foo",
            "kind": "lang",
            "source_id": 0,
            "en_us": "Use %s",
            "old_translation": "使用",
            "ai_translation": "使用",
            "draft": "使用 %s",
            "issues": ("少了 1 個「%s」",),
        },
    )
    page = mock_page()
    closed_counts = []
    reviewer = RepairReviewer(
        page, str(path), "run-applied", on_close=closed_counts.append
    )
    reviewer.open()

    store.save_review_state(path, "run-applied", item_index, "applied", "使用 %s")
    reviewer._render()  # mirror the refresh after a successful apply
    assert reviewer.status.value.startswith("✓ 已套用至原來源 #0")
    assert reviewer.body.controls[2] is reviewer.status
    reviewer._close()

    assert store.status_counts(path, "run-applied") == {"applied": 1}
    assert closed_counts == [{"applied": 1}]


def test_inspector_reports_unresolved_count_after_reviewer_closes(monkeypatch):
    from app.views.moddb import result_inspector as inspector_module

    callback_counts = []
    close_callback = {}

    def open_reviewer(_page, _path, _run_id, *, on_close):
        close_callback["callback"] = on_close

    monkeypatch.setattr(inspector_module, "open_repair_reviewer", open_reviewer)
    inspector = TranslationResultInspector(
        mock_page(), on_repair_review_changed=callback_counts.append
    )
    inspector.set_mode("repair", "quality_mismatch")
    inspector.set_repair_results(
        {
            "review_store_path": "review.db",
            "review_run_id": "run-1",
            "reviewable_results": 4,
        }
    )

    inspector.inspect()
    close_callback["callback"]({"applied": 2, "pending": 1, "kept_old": 1})

    assert callback_counts == [2]


def test_switching_review_item_replaces_the_previous_status_message(tmp_path):
    path = store.create_run(tmp_path / "review.db", "run-switch")
    for key, draft in (("repair.first", "已套用 %s"), ("repair.second", "草稿")):
        store.append_item(
            path,
            "run-switch",
            {
                "key": key,
                "version": "1.21.1",
                "mod_id": "foo",
                "kind": "lang",
                "source_id": 0,
                "en_us": "Use %s",
                "old_translation": "舊譯文",
                "ai_translation": draft,
                "draft": draft,
                "issues": (),
            },
        )
    page = mock_page()
    reviewer = RepairReviewer(page, str(path), "run-switch")
    reviewer.status_filter = "all"
    reviewer.filtered_count = 2
    reviewer._load_index_page()
    reviewer._render()

    reviewer.status.value = "上一筆的套用成功訊息"
    reviewer._move(1)

    assert reviewer.status.value == "待審查：尚未套用。"
    assert reviewer.draft.value == "草稿"
    assert all(
        "格式差異" not in str(getattr(control, "value", ""))
        for control in reviewer.body.controls
    )


def test_empty_filter_shows_empty_state_and_can_switch_back(tmp_path):
    path = store.create_run(tmp_path / "review-empty-filter.db", "run-empty-filter")
    store.append_item(
        path,
        "run-empty-filter",
        {
            "key": "repair.pending",
            "version": "1.21.1",
            "mod_id": "foo",
            "kind": "lang",
            "source_id": 0,
            "en_us": "Use %s",
            "old_translation": "使用",
            "ai_translation": "使用 %s",
        },
    )
    page = mock_page()
    reviewer = RepairReviewer(page, str(path), "run-empty-filter")
    reviewer.open()

    reviewer._change_filter(
        type("Event", (), {"control": type("Control", (), {"value": "applied"})()})()
    )

    assert reviewer.item_indices == []
    assert reviewer.item is None
    assert "沒有項目" in reviewer.status.value
    assert len(reviewer.body.controls) == 3

    reviewer._change_filter(
        type("Event", (), {"control": type("Control", (), {"value": "pending"})()})()
    )

    assert reviewer.item is not None
    assert reviewer.item["key"] == "repair.pending"


def test_close_during_apply_does_not_overwrite_background_applied_state(
    tmp_path, monkeypatch
):
    import asyncio
    import threading

    from app.views.moddb import repair_reviewer as reviewer_module

    path = store.create_run(tmp_path / "review-close-race.db", "run-close-race")
    item_index = store.append_item(
        path,
        "run-close-race",
        {
            "key": "repair.race",
            "version": "1.21.1",
            "mod_id": "foo",
            "kind": "lang",
            "source_id": 0,
            "en_us": "Use %s",
            "old_translation": "使用",
            "ai_translation": "使用 %s",
            "draft": "使用 %s",
        },
    )
    entered = threading.Event()
    release = threading.Event()

    def apply_then_save(*args):
        entered.set()
        assert release.wait(timeout=5)
        store.save_review_state(
            path, "run-close-race", item_index, "applied", "使用 %s"
        )
        return "applied"

    monkeypatch.setattr(
        reviewer_module, "apply_and_record_repair_review", apply_then_save
    )
    page = mock_page()
    reviewer = RepairReviewer(page, str(path), "run-close-race")
    reviewer.open()
    reviewer._apply()

    async def scenario():
        task_coro, args = page._tasks.pop()
        task = asyncio.create_task(task_coro(*args))
        assert await asyncio.to_thread(entered.wait, 5)
        reviewer._close()
        assert not reviewer.closed
        release.set()
        await task

    asyncio.run(scenario())

    assert (
        store.load_item(path, "run-close-race", item_index)["review_status"]
        == "applied"
    )
    assert reviewer.applying is False
    reviewer._close()
    assert reviewer.closed


def _identity(db_path, **overrides):
    item = {
        "database_identity": str(db_path),
        "database_file_id": 1,
        "version": "1.21.1",
        "mod_id": "fancymenu",
        "kind": "lang",
        "key": "tooltip",
        "en_us": "Will prevent\nthe element",
        "old_translation": "防止元素",
        "ai_translation": "防止元素。",
        "source_id": 2,
    }
    item.update(overrides)
    return item


def test_identical_items_from_different_sources_are_reviewed_as_one(tmp_path):
    path = store.create_run(tmp_path / "group.db", "run-g")
    first = store.append_item(path, "run-g", _identity(tmp_path, source_id=2))
    second = store.append_item(path, "run-g", _identity(tmp_path, source_id=4))
    # 舊譯文不同、或 AI 草稿不同的不能併進同一筆
    store.append_item(
        path, "run-g", _identity(tmp_path, source_id=1, old_translation="別的")
    )
    store.append_item(
        path, "run-g", _identity(tmp_path, source_id=3, ai_translation="另一版")
    )

    assert store.index_count(path, "run-g", "pending") == 4
    assert store.index_count(path, "run-g", "pending", grouped=True) == 3
    assert store.indices(path, "run-g", "pending", grouped=True)[0] == first
    assert store.group_indices(path, "run-g", second) == [first, second]
    assert store.status_counts(path, "run-g", grouped=True) == {"pending": 3}
    assert store.status_counts(path, "run-g") == {"pending": 4}


def test_items_without_identity_fields_stay_single(tmp_path):
    path = store.create_run(tmp_path / "single.db", "run-s")
    store.append_item(path, "run-s", {"key": "a", "source_id": 0})
    store.append_item(path, "run-s", {"key": "a", "source_id": 1})

    assert store.index_count(path, "run-s", grouped=True) == 2
    assert store.group_indices(path, "run-s", 1) == [1]


def test_old_store_without_group_column_is_migrated_on_use(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE repair_review (run_id TEXT NOT NULL, item_index INTEGER "
            "NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
            "draft TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (run_id, item_index))"
        )
        conn.execute(
            "INSERT INTO repair_review(run_id,item_index,payload) VALUES ('r',1,'{}')"
        )

    assert store.index_count(path, "r", "pending", grouped=True) == 1
    assert store.indices(path, "r", "pending", grouped=True) == [1]


def test_reviewer_lists_merged_sources_and_keep_old_covers_the_whole_group(tmp_path):
    path = store.create_run(tmp_path / "ui.db", "run-ui")
    store.append_item(path, "run-ui", _identity(tmp_path, source_id=2))
    store.append_item(path, "run-ui", _identity(tmp_path, source_id=4))
    reviewer = RepairReviewer(mock_page(), str(path), "run-ui")
    reviewer.filtered_count = store.index_count(path, "run-ui", "pending", grouped=True)
    reviewer._load_index_page()
    reviewer.page_size = 100

    reviewer._render()

    assert reviewer.filtered_count == 1
    assert "#2、#4" in reviewer._source_text(reviewer.item)
    assert "共 2 個來源" in reviewer._source_text(reviewer.item)

    reviewer._keep_old()

    assert store.status_counts(path, "run-ui") == {"kept_old": 2}


def test_matching_items_never_reaches_rows_with_other_old_text_or_draft(tmp_path):
    path = store.create_run(tmp_path / "match.db", "run-m")
    mine = _identity(tmp_path, source_id=2)
    store.append_item(path, "run-m", mine)
    same = store.append_item(path, "run-m", _identity(tmp_path, source_id=4))
    store.append_item(
        path, "run-m", _identity(tmp_path, source_id=1, old_translation="別的")
    )
    store.append_item(
        path, "run-m", _identity(tmp_path, source_id=3, ai_translation="另一版")
    )

    matched = store.matching_items(path, "run-m", mine)

    assert sorted(item["item_index"] for item in matched) == [1, same]


def test_matching_items_ignores_items_without_a_reliable_identity(tmp_path):
    path = store.create_run(tmp_path / "noid.db", "run-n")
    store.append_item(path, "run-n", {"key": "a", "source_id": 0})
    store.append_item(path, "run-n", {"key": "a", "source_id": 1})

    assert store.matching_items(path, "run-n", {"key": "a", "source_id": 0}) == []
