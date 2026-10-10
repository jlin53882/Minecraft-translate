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
