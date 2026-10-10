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


def test_keep_old_uses_defined_semantic_color():
    reviewer = RepairReviewer(mock_page(), "unused.db", "run-1")
    reviewer._render = lambda: None

    reviewer._keep_old()

    assert reviewer.status.value == "已保留舊譯文；沒有寫入翻譯資料，也沒有新增歷史。"
    assert reviewer.status.color == C.EM


def test_draft_box_is_taller_and_checks_format_while_editing(tmp_path):
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
    assert reviewer.apply_button.disabled is True
    assert "少了 1 個「%s」" in reviewer.format_status.value

    reviewer.draft.value = "翻譯 %s"
    reviewer._draft_changed()

    assert reviewer.apply_button.disabled is False
    assert reviewer.format_status.value == "格式檢查通過；可確認套用。"
    saved = store.load_item(path, "run-live-check", item_index)
    assert saved["draft"] == "翻譯 %s"
