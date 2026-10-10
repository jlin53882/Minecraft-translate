from app.services_impl import moddb_repair_review_store as store
from app.ui.design import C
from app.views.moddb.repair_reviewer import RepairReviewer
from app.views.moddb.result_inspector import TranslationResultInspector
from tests.conftest import mock_page


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
    reviewer._render = lambda: None

    reviewer._keep_old()

    assert reviewer.status.value == "已保留舊譯文；沒有寫入翻譯資料，也沒有新增歷史。"
    assert reviewer.status.color == C.EM


def test_draft_box_allows_human_confirmed_apply_without_format_check(tmp_path):
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

    assert reviewer.draft.height == 240
    assert reviewer.apply_button.disabled is False
    assert all(
        "格式差異" not in str(getattr(control, "value", ""))
        for control in reviewer.body.controls
    )

    reviewer.draft.value = "翻譯文字，保留由人工確認的內容"
    reviewer._draft_changed()

    assert reviewer.apply_button.disabled is False
    saved = store.load_item(path, "run-live-check", item_index)
    assert saved["draft"] == "翻譯文字，保留由人工確認的內容"

    reviewer._apply()

    assert page._tasks
    assert reviewer.status.value == "正在重新驗證來源與 revision…"
    assert reviewer.apply_button.disabled is True


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

    assert reviewer.status.value == ""
    assert reviewer.draft.value == "草稿"
    assert all(
        "格式差異" not in str(getattr(control, "value", ""))
        for control in reviewer.body.controls
    )
