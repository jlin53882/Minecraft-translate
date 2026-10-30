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
    assert isinstance(body, ft.Row)
    assert len(body.controls) == 2
    assert all(isinstance(column, ft.Column) for column in body.controls)
    assert len(body.controls[0].controls) == 4
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

    view._sync_progress_stage(0.1, "RUNNING")
    assert view.progress_panel.steps[0].status == "running"
    view._sync_progress_stage(0.5, "RUNNING")
    assert view.progress_panel.steps[1].status == "running"
    view._sync_progress_stage(0.8, "RUNNING")
    assert view.progress_panel.steps[2].status == "running"
    view._sync_progress_stage(0.98, "RUNNING")
    assert view.progress_panel.steps[3].status == "running"
