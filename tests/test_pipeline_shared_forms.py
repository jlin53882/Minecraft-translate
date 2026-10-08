"""Shared pipeline form factories must remain the single source of UI structure."""

import flet as ft
import pytest

from app.views.pipeline import (
    pipeline_bundle_dialog,
    pipeline_extract_dialog,
    pipeline_forms,
    pipeline_merge_dialog,
    pipeline_one_click_bundle_widgets,
    pipeline_one_click_dialog,
    pipeline_translate_dialog,
)


@pytest.mark.parametrize(
    ("standalone", "wizard", "factory_name"),
    [
        (pipeline_extract_dialog, pipeline_one_click_dialog, "build_extract_form"),
        (pipeline_merge_dialog, pipeline_one_click_dialog, "build_merge_form"),
        (pipeline_translate_dialog, pipeline_one_click_dialog, "build_translate_form"),
        (pipeline_bundle_dialog, pipeline_one_click_dialog, "build_bundle_form"),
    ],
)
def test_standalone_and_wizard_import_the_same_form_factory(
    standalone, wizard, factory_name
):
    factory = getattr(pipeline_forms, factory_name)

    assert getattr(standalone, factory_name) is factory
    assert getattr(wizard, factory_name) is factory


def test_bundle_entry_points_import_the_same_version_picker_factory():
    assert (
        pipeline_bundle_dialog.build_version_picker
        is pipeline_forms.build_version_picker
    )
    assert (
        pipeline_one_click_bundle_widgets.build_version_picker
        is pipeline_forms.build_version_picker
    )


def test_shared_factory_creates_fresh_control_instances_per_entry():
    state = pipeline_forms.ExtractFormState(lang_codes={"en_us": True})
    standalone = pipeline_forms.build_extract_form(
        path_section=ft.Container(), state=state, language_codes=("en_us",)
    )
    wizard = pipeline_forms.build_extract_form(
        path_section=ft.Container(), state=state, language_codes=("en_us",)
    )

    assert standalone.content is not wizard.content
    assert standalone.mode is not wizard.mode
    assert standalone.lang_checks["en_us"] is not wizard.lang_checks["en_us"]
