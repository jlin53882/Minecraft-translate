"""KubeJS／FTB 流水線工作台的 UI 契約測試。"""

import asyncio
import threading

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


def test_kubejs_steps_share_one_run_button_list_and_wizard():
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())

    assert len(view._run_buttons) == 5  # 四個步驟按鈕＋一鍵執行
    assert view._one_click_button in view._run_buttons
    assert view._wizard is not None


def test_wizard_blocks_next_until_sources_are_analyzed_and_selected():
    from app.views.pipeline.kubejs_ftb_wizard import KubejsFtbWizard

    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())
    wizard = view._wizard
    assert isinstance(wizard, KubejsFtbWizard)

    assert wizard._step_problem(1) is not None  # 尚未分析來源
    view._candidates = ["/a", "/b"]
    assert wizard._step_problem(1) is not None  # 多個候選未選
    view.root_dropdown.value = "/a"
    view.include_kubejs.value = False
    view.include_ftb.value = False
    assert wizard._step_problem(1) is not None  # 來源類型都未選
    view.include_kubejs.value = True
    assert wizard._step_problem(1) is None
    assert not hasattr(view, "version_field")  # 遊戲版本不在第 1 步


def test_game_version_is_chosen_once_in_step_two(monkeypatch):
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())
    options = view.lm_db_options
    monkeypatch.setattr(options, "_refresh_database_state", lambda _s: None)
    options._versions = ["1.20.1", "1.21.1"]
    options.version_field.options = [
        ft.dropdown.Option(v, v) for v in options._versions
    ]

    assert view._wizard._step_problem(2) is not None  # 尚未選版本
    assert view._run_blocker(publish=False) is not None

    view._apply_detected_version("1.21.1")  # 分析來源偵測到的版本預選
    assert view.game_version() == "1.21.1"
    assert view._wizard._step_problem(2) is None
    assert view.translation_db_choice()[1] == "1.21.1"  # LM 目標版本與遊戲版本相同

    options._on_version_changed()  # 使用者自選後，不會被之後的偵測覆蓋
    options.version_field.value = "1.20.1"
    view._apply_detected_version("1.21.1")
    assert view.game_version() == "1.20.1"


def test_detected_version_missing_from_database_is_not_preselected(monkeypatch):
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())
    options = view.lm_db_options
    monkeypatch.setattr(options, "_refresh_database_state", lambda _s: None)
    options._versions = ["1.20.1"]

    view._apply_detected_version("1.21.1")

    assert view.game_version() == ""
    assert "資料庫沒有這個版本" in options.info.value


def test_one_click_requires_output_destination_before_opening_wizard(monkeypatch):
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())
    opened: list[bool] = []
    monkeypatch.setattr(view._wizard, "open", lambda: opened.append(True))
    view.source_field.value = "/pack"

    view.output_field.value = ""
    view._one_click_clicked()
    assert opened == []

    view.output_field.value = "/overlay"
    view._one_click_clicked()
    assert opened == [True]


def test_wizard_final_step_runs_publish_directly_without_preview(monkeypatch):
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())
    calls: list[tuple[bool, bool]] = []

    async def fake_start(*, dry_run: bool, publish: bool) -> None:
        calls.append((dry_run, publish))

    monkeypatch.setattr(view, "_start", fake_start)

    import asyncio

    asyncio.run(view._run_one_click_publish())

    assert calls == [(False, True)]
    assert not hasattr(view, "_offer_publish_confirmation")


def test_discover_discards_version_result_when_source_changes_while_detecting(
    monkeypatch,
):
    view = KubejsFtbPipelineView(mock_page(), mock_filepicker())
    view.source_field.value = "source-a"
    started = threading.Event()
    release = threading.Event()
    candidates_called = False

    def detect(source):
        assert source == "source-a"
        started.set()
        assert release.wait(timeout=2)
        return "1.21.1"

    def discover(_source):
        nonlocal candidates_called
        candidates_called = True
        return ["source-a"]

    monkeypatch.setattr(
        "app.views.kubejs_ftb_pipeline_view.detect_minecraft_version", detect
    )
    monkeypatch.setattr(
        "app.views.kubejs_ftb_pipeline_view.discover_kubejs_ftb_pack_candidates",
        discover,
    )

    async def exercise():
        task = asyncio.create_task(view._discover())
        assert await asyncio.to_thread(started.wait, 2)
        view.source_field.value = "source-b"
        view._source_changed()
        release.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(exercise())

    assert view._detected_version is None
    assert view._candidates == []
    assert candidates_called is False
