"""Pipeline Merge Dialog constrained-height layout contracts."""

from __future__ import annotations

from types import SimpleNamespace

import flet as ft
import pytest

from app.views.pipeline import pipeline_merge_dialog


@pytest.mark.parametrize(("width", "height"), ((1100, 720), (900, 700)))
def test_pipeline_merge_dialog_bounds_scrollable_body_and_keeps_actions_separate(
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    height: int,
) -> None:
    """Regression: a long body must scroll inside the viewport, not cover actions."""
    page = SimpleNamespace(
        width=width,
        height=height,
        overlay=[],
        update=lambda: None,
    )
    monkeypatch.setattr(pipeline_merge_dialog, "load_config", dict)

    pipeline_merge_dialog.open_merge_dialog(
        page=page,
        file_picker=None,
        input_path="input",
        output_path="output",
        lang_code_checks={},
        on_run_merge=lambda *_args, **_kwargs: None,
        show_snack_bar=lambda *_args, **_kwargs: None,
    )

    dialog = page.overlay[0]
    body = dialog.content

    assert body.height is not None
    assert 0 < body.height < height
    assert height * 0.5 <= body.height <= height * 0.7
    assert body.content.scroll == ft.ScrollMode.AUTO
    assert dialog.scrollable is False
    assert len(dialog.actions) == 3
    assert all(action not in body.content.controls for action in dialog.actions)


def test_pipeline_merge_cancel_action_closes_and_reopens_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正式取消 callback 關閉本次 Dialog 後，入口可建立新的開啟實例。"""
    updates: list[None] = []
    page = SimpleNamespace(
        width=1100,
        height=720,
        overlay=[],
        update=lambda: updates.append(None),
    )
    monkeypatch.setattr(pipeline_merge_dialog, "load_config", dict)

    def open_dialog() -> ft.AlertDialog:
        """透過正式 Pipeline Merge builder 建立新 Dialog。"""
        pipeline_merge_dialog.open_merge_dialog(
            page=page,
            file_picker=None,
            input_path="input",
            output_path="output",
            lang_code_checks={},
            on_run_merge=lambda *_args, **_kwargs: None,
            show_snack_bar=lambda *_args, **_kwargs: None,
        )
        return page.overlay[-1]

    first_dialog = open_dialog()
    cancel_action = first_dialog.actions[0]
    assert callable(cancel_action.on_click)
    cancel_action.on_click(None)
    assert first_dialog.open is False

    reopened_dialog = open_dialog()

    assert reopened_dialog is not first_dialog
    assert reopened_dialog.open is True
    assert page.overlay == [reopened_dialog]  # 關閉後移出 overlay
    assert len(updates) == 4  # 開啟、關閉、移除、再開啟
