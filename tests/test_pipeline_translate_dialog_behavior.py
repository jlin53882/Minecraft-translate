"""open_translate_dialog 的行為層測試。"""

from __future__ import annotations

import os

import flet as ft
import pytest

from app.views.pipeline import pipeline_translate_dialog as mod
from tests.conftest import _make_page, mock_filepicker


def _walk(control):
    yield control
    for attr in ("controls", "actions"):
        for child in getattr(control, attr, None) or []:
            yield from _walk(child)
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)


def _find(dialog, cls, label=None):
    for c in _walk(dialog):
        if isinstance(c, cls) and (label is None or getattr(c, "label", None) == label):
            return c
    raise AssertionError(f"找不到 {cls.__name__} {label}")


def _button(dialog, label):
    for a in dialog.actions:
        if getattr(a, "content", None) == label:
            return a
    raise AssertionError(f"找不到按鈕 {label}")


def _switch(dialog, prefix):
    for c in _walk(dialog):
        if isinstance(c, ft.Switch) and c.label.startswith(prefix):
            return c
    raise AssertionError(prefix)


def _drain(page):
    tasks, page._tasks = page._tasks, []
    for coro, args in tasks:
        result = coro(*args)
        if result is not None:
            try:
                result.send(None)
            except StopIteration:
                pass


class _Env:
    def __init__(self, monkeypatch, tmp_path):
        self.page = _make_page(width=1000, height=700)
        self.src = tmp_path / "src"
        self.src.mkdir()
        self.out = tmp_path / "out"
        self.out.mkdir()
        # 翻譯目標預設為 {output}/locale_sort/_整理輸出/<待翻譯整理資料夾>（需存在才能開始）
        self.organized = self.out / "locale_sort" / "_整理輸出" / "待翻譯整理需翻譯"
        self.organized.mkdir(parents=True)
        self.snacks: list[str] = []
        self.runs: list[dict] = []
        self.cfg: dict = {}
        self.picker = mock_filepicker()
        monkeypatch.setattr(mod, "load_config", lambda: self.cfg)

    def open(self, input_path=None, output_path=None):
        mod.open_translate_dialog(
            page=self.page,
            file_picker=self.picker,
            input_path=str(self.src) if input_path is None else input_path,
            output_path=str(self.out) if output_path is None else output_path,
            on_start_translate=lambda **kw: self.runs.append(kw),
            show_snack_bar=self.snacks.append,
        )
        return self.page.overlay[-1]

    def fields(self, dialog):
        return (
            _find(dialog, ft.TextField, "翻譯目標"),
            _find(dialog, ft.TextField, "輸出目錄"),
        )


@pytest.fixture
def env(monkeypatch, tmp_path):
    return _Env(monkeypatch, tmp_path)


def test_initial_state(env):
    dialog = env.open()
    assert dialog.open is True and dialog.modal is True
    assert dialog in env.page.overlay
    tin, tout = env.fields(dialog)
    # 預設值由輸出根目錄推算；管線頁的 Mod 來源不會被當成翻譯目標
    assert tin.value == str(env.organized)
    assert tout.value == str(env.out / "lm_translate")
    assert _switch(dialog, "Dry Run").value is False
    assert _switch(dialog, "寫入新快取").value is True


def test_mod_source_is_never_used_as_translate_input(env):
    """原本把 Mod 來源當成翻譯目標、輸出根目錄當成輸出目錄，預設執行會翻譯整個 Mods 資料夾。"""
    for given in ("", str(env.src)):
        dialog = env.open(input_path=given)
        tin, tout = env.fields(dialog)
        assert tin.value == os.path.join(
            str(env.out), "locale_sort", "_整理輸出", "待翻譯整理需翻譯"
        )
        assert tout.value == str(env.out / "lm_translate")


def test_organized_folder_name_comes_from_config_and_reopen_is_fresh(env):
    first = env.open(input_path="")
    env.cfg = {"lang_merger": {"pending_organized_folder_name": "自訂名稱"}}
    second = env.open(input_path="")
    assert env.fields(first)[0].value.endswith("待翻譯整理需翻譯")
    assert env.fields(second)[0].value == os.path.join(
        str(env.out), "locale_sort", "_整理輸出", "自訂名稱"
    )


def test_start_passes_exact_kwargs_and_closes(env):
    dialog = env.open()
    _button(dialog, "確定執行").on_click(None)
    assert dialog.open is False
    assert env.snacks == []
    assert env.runs == [
        {
            "input_dir": str(env.organized),
            "output_dir": str(env.out / "lm_translate"),
            "dry_run": False,
            "write_new_cache": True,
        }
    ]


def test_toggled_switches_are_passed(env):
    dialog = env.open()
    _switch(dialog, "Dry Run").value = True
    _switch(dialog, "寫入新快取").value = False
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0]["dry_run"] is True
    assert env.runs[0]["write_new_cache"] is False


def test_start_strips_whitespace(env):
    dialog = env.open()
    tin, tout = env.fields(dialog)
    tin.value = f"  {env.src}  "
    tout.value = f"  {env.out}  "
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0]["input_dir"] == str(env.src)
    assert env.runs[0]["output_dir"] == str(env.out)


def test_start_rejects_missing_input_dir(env, tmp_path):
    dialog = env.open()
    env.fields(dialog)[0].value = str(tmp_path / "nope")
    _button(dialog, "確定執行").on_click(None)
    assert env.snacks == ["⚠️ 翻譯目標資料夾不存在"]
    assert env.runs == []
    assert dialog.open is True


def test_blank_fields_fall_back_to_defaults_even_if_not_existing(env):
    dialog = env.open(input_path="")
    tin, tout = env.fields(dialog)
    default_in = tin.value
    default_out = os.path.join(str(env.out), "lm_translate")
    tin.value = ""
    tout.value = "  "
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0]["input_dir"] == default_in
    assert env.runs[0]["output_dir"] == default_out
    assert dialog.open is False


def test_output_dir_is_not_validated_for_existence(env, tmp_path):
    dialog = env.open()
    env.fields(dialog)[1].value = str(tmp_path / "new_out")
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0]["output_dir"] == str(tmp_path / "new_out")


def test_cancel_closes_without_running_and_reopen_is_new_dialog(env):
    dialog = env.open()
    _button(dialog, "取消").on_click(None)
    assert dialog.open is False and env.runs == []
    again = env.open()
    assert again is not dialog and again.open is True
    assert env.page.overlay == [again]  # 關閉後移出 overlay，不累積
