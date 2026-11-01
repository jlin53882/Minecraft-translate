"""KubeJS／FTB 流水線工作台的 UI 契約測試。"""

import flet as ft

from app.views.kubejs_ftb_pipeline_view import KubejsFtbPipelineView
from app.views.pipeline.pipeline_workbench import (
    PipelineStepSpec,
    build_pipeline_steps_card,
)
from tests.conftest import mock_filepicker, mock_page


def test_kubejs_pipeline_uses_one_click_workbench_layout():
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())

    assert not hasattr(view, "tabs")
    assert len(view.controls) == 1
    workbench = view.controls[0]
    assert isinstance(workbench, ft.Column)
    body = workbench.controls[1]
    assert isinstance(body, ft.ResponsiveRow)
    assert len(body.controls) == 2
    assert all(isinstance(column, ft.Column) for column in body.controls)
    assert len(body.controls[0].controls) == 2
    assert len(body.controls[1].controls) == 2
    assert view.progress_panel.container.visible is False
    assert view.log_view is view.progress_panel.log_view


def test_kubejs_pipeline_reuses_shared_progress_steps():
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())

    assert [step.name for step in view.progress_panel.steps] == [
        "分析來源",
        "同步 ModDB",
        "LM 翻譯",
        "輸出 overlay",
    ]
    assert view.cancel_button is view.progress_panel.cancel_button
    assert view.cancel_button.visible is False


def test_shared_pipeline_steps_card_uses_the_same_row_contract_without_actions():
    card = build_pipeline_steps_card(
        [
            PipelineStepSpec(
                1,
                "分析來源",
                "辨識來源",
                ft.Icons.FIND_IN_PAGE_OUTLINED,
                "dia",
            ),
            PipelineStepSpec(
                2,
                "同步 ModDB",
                "寫入共用資料庫",
                ft.Icons.SYNC,
                "ench",
            ),
        ]
    )

    assert isinstance(card, ft.Container)
    assert len(card.content.controls) == 2
    assert all(len(row.content.controls) == 2 for row in card.content.controls)


def test_kubejs_pipeline_maps_session_progress_to_workflow_stage():
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())
    view._running = True
    view._current_publish = True

    view._sync_progress_stage(0.1, "RUNNING")
    assert view.progress_panel.steps[0].status == "running"
    view._sync_progress_stage(0.5, "RUNNING")
    assert view.progress_panel.steps[1].status == "running"
    view._sync_progress_stage(0.8, "RUNNING")
    assert view.progress_panel.steps[2].status == "running"
    view._sync_progress_stage(0.98, "RUNNING")
    assert view.progress_panel.steps[3].status == "running"


def test_kubejs_sync_only_run_never_marks_overlay_stage():
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())
    view._running = True
    view._current_publish = False

    view._sync_progress_stage(0.98, "RUNNING")

    assert view.progress_panel.steps[2].status == "running"
    assert view.progress_panel.steps[3].status != "running"


def test_kubejs_steps_share_one_settings_dialog_and_four_run_buttons():
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())

    assert len(view._run_buttons) == 5
    assert view._settings_dialog is not None
    assert view._settings_button is not None


def _preview_summary(*, conflicts: int = 0) -> dict:
    return {
        "source_root": "/pack",
        "coverage": {"files_read": 3, "files_seen": 3, "items_found": 9},
        "sync": {"items_to_sync": 4, "items_unchanged": 5},
        "output": {
            "output_root": "/overlay",
            "entries_applied": 4,
            "conflicts": [{"path": f"c{i}"} for i in range(conflicts)],
            "unapplied": [],
        },
    }


def test_one_click_confirmation_enables_publish_when_preview_is_clean():
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())

    view._offer_publish_confirmation(True, _preview_summary())

    assert view._publish_confirm_button.disabled is False


def test_one_click_confirmation_blocks_publish_on_overlay_conflicts():
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())

    view._offer_publish_confirmation(True, _preview_summary(conflicts=2))

    assert view._publish_confirm_button.disabled is True


def test_one_click_confirmation_is_not_offered_after_failed_preview():
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())

    view._offer_publish_confirmation(False, _preview_summary())

    assert view._publish_dialog is None


class _FinishedPreviewSession:
    def __init__(self, summary: dict):
        self._snapshot = {
            "status": "DONE",
            "progress": 1.0,
            "logs": [],
            "summary": summary,
        }

    def snapshot(self) -> dict:
        return self._snapshot


def test_one_click_preview_completion_opens_publish_confirmation():
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())
    view._running = True
    view._one_click_stage = "preview"
    preview = _preview_summary()
    preview["state"] = "preview"
    view.session = _FinishedPreviewSession(preview)

    view._sync_session()

    assert view._one_click_stage is None
    assert view._publish_dialog is not None
    assert view._publish_confirm_button.disabled is False


def test_plain_preview_completion_does_not_open_publish_confirmation():
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())
    view._running = True
    view._one_click_stage = None
    view.session = _FinishedPreviewSession(_preview_summary())

    view._sync_session()

    assert view._publish_dialog is None
