"""open_merge_dialog 的行為層測試。

透過公開入口建立對話框，直接操作控件與按鈕 on_click，驗證初始狀態、輸入驗證、
選項相依、傳給 on_run_merge 的參數、關閉狀態與重新開啟時重新讀取 config。
"""

from __future__ import annotations

from types import SimpleNamespace

import flet as ft
import pytest

from app.views.pipeline import pipeline_merge_dialog as mod
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
        self.page = _make_page(width=1200, height=800)
        self.src = tmp_path / "src"
        self.src.mkdir()
        self.out = tmp_path / "out"
        self.out.mkdir()
        self.snacks: list[str] = []
        self.runs: list[tuple] = []
        self.cfg: dict = {}
        self.picker = mock_filepicker()
        monkeypatch.setattr(mod, "load_config", lambda: self.cfg)

    def open(self, **kwargs):
        kwargs.setdefault("input_path", str(self.src))
        kwargs.setdefault("output_path", str(self.out))
        kwargs.setdefault("lang_code_checks", {"zh_tw": SimpleNamespace(value=True)})
        mod.open_merge_dialog(
            page=self.page,
            file_picker=self.picker,
            on_run_merge=lambda *a: self.runs.append(a),
            show_snack_bar=self.snacks.append,
            **kwargs,
        )
        return self.page.overlay[-1]


@pytest.fixture
def env(monkeypatch, tmp_path):
    return _Env(monkeypatch, tmp_path)


def _set_mode(dialog, mode):
    group = _find(dialog, ft.RadioGroup)
    group.value = mode
    group.on_change(None)


def test_initial_state_and_defaults(env):
    dialog = env.open()
    assert dialog.open is True and dialog in env.page.overlay
    assert dialog.modal is True
    assert _find(dialog, ft.TextField, "Mod 來源").value == str(env.src)
    assert _find(dialog, ft.TextField, "輸出目錄").value == str(env.out)
    assert _find(dialog, ft.Checkbox).value is True
    zh = _find(dialog, ft.Switch, "處理 zh_cn 檔案")
    skip = _find(dialog, ft.Switch, "允許 zh_cn 觸發跳過 en_us")
    assert zh.value is True
    assert skip.value is False and skip.disabled is False
    fields = [c for c in _walk(dialog) if isinstance(c, ft.TextField) and c.width]
    assert sorted(f.value for f in fields) == ["0.5", "2"]
    assert _find(dialog, ft.RadioGroup).value == "folder"


def test_config_values_seed_patchouli_and_threshold_fields(env):
    env.cfg = {
        "lang_merger": {
            "patchouli_skip_en_us_when_zh_cn_exists": True,
            "patchouli_effective_translation_threshold": 0.8,
            "zh_en_letter_threshold": 5,
        }
    }
    dialog = env.open()
    assert _find(dialog, ft.Switch, "允許 zh_cn 觸發跳過 en_us").value is True
    values = {c.value for c in _walk(dialog) if isinstance(c, ft.TextField) and c.width}
    assert values == {"0.8", "5"}


def test_reopen_reads_fresh_config(env):
    first = env.open()
    assert _find(first, ft.Switch, "允許 zh_cn 觸發跳過 en_us").value is False
    env.cfg = {"lang_merger": {"patchouli_skip_en_us_when_zh_cn_exists": True}}
    second = env.open()
    assert second is not first
    assert _find(second, ft.Switch, "允許 zh_cn 觸發跳過 en_us").value is True


def test_zh_cn_switch_off_disables_and_resets_patchouli_options(env):
    env.cfg = {"lang_merger": {"patchouli_skip_en_us_when_zh_cn_exists": True}}
    dialog = env.open()
    zh = _find(dialog, ft.Switch, "處理 zh_cn 檔案")
    skip = _find(dialog, ft.Switch, "允許 zh_cn 觸發跳過 en_us")
    threshold = next(
        c for c in _walk(dialog) if isinstance(c, ft.TextField) and c.width == 100
    )
    before = env.page.updated
    zh.value = False
    zh.on_change(None)
    assert skip.disabled is True and skip.value is False
    assert threshold.disabled is True
    assert env.page.updated == before + 1

    zh.value = True
    zh.on_change(None)
    assert skip.disabled is False and threshold.disabled is False
    assert skip.value is False  # 關閉時已被重設，重新開啟不會自動還原


def test_input_mode_switch_toggles_row_visibility(env):
    dialog = env.open()
    folder_row = next(
        c
        for c in _walk(dialog)
        if isinstance(c, ft.Container)
        and any(isinstance(x, ft.TextField) and x.label == "Mod 來源" for x in _walk(c))
        and c is not dialog.content
    )
    zip_row = next(
        c
        for c in _walk(dialog)
        if isinstance(c, ft.Container)
        and any(isinstance(x, ft.ListView) for x in _walk(c))
        and c is not dialog.content
        and c.content is not dialog.content.content
        and isinstance(c.content, ft.Column)
    )
    assert folder_row.visible is True and zip_row.visible is False
    _set_mode(dialog, "zip")
    assert folder_row.visible is False and zip_row.visible is True
    _set_mode(dialog, "folder")
    assert folder_row.visible is True and zip_row.visible is False


@pytest.mark.parametrize(
    ("which", "expected"),
    [
        ("src_empty", "⚠️ 輸入來源為必填欄位"),
        ("src_missing", "⚠️ 輸入來源資料夾不存在"),
        ("out_empty", "⚠️ 輸出目錄為必填欄位"),
        ("out_is_file", "⚠️ 輸出目錄路徑是檔案，不是資料夾"),
    ],
)
def test_start_rejects_bad_directories(env, tmp_path, which, expected):
    dialog = env.open()
    src = _find(dialog, ft.TextField, "Mod 來源")
    out = _find(dialog, ft.TextField, "輸出目錄")
    if which == "src_empty":
        src.value = "   "
    elif which == "src_missing":
        src.value = str(tmp_path / "nope")
    elif which == "out_empty":
        out.value = ""
    else:
        file_path = tmp_path / "out.txt"
        file_path.write_text("x")
        out.value = str(file_path)
    _button(dialog, "確定執行").on_click(None)
    assert env.snacks == [expected]
    assert env.runs == []
    assert dialog.open is True


def test_start_accepts_a_new_output_dir_without_creating_it(env, tmp_path):
    """對話框只檢查路徑；建立交給合併服務，取消時才分得出「新建的」並清掉。"""
    dialog = env.open()
    out = _find(dialog, ft.TextField, "輸出目錄")
    out.value = str(tmp_path / "new" / "out")
    _button(dialog, "確定執行").on_click(None)
    assert not (tmp_path / "new" / "out").exists()
    assert not any("輸出目錄" in m for m in env.snacks)


def test_start_rejects_when_no_language_code_selected(env):
    dialog = env.open(lang_code_checks={"zh_tw": SimpleNamespace(value=False)})
    _button(dialog, "確定執行").on_click(None)
    assert env.snacks == ["⚠️ 請至少選擇一個語言代碼"]
    assert env.runs == []
    assert dialog.open is True


def test_start_folder_mode_passes_exact_arguments_and_closes(env):
    checks = {
        "zh_tw": SimpleNamespace(value=True),
        "zh_hk": SimpleNamespace(value=False),
        "ja_jp": SimpleNamespace(value=True),
    }
    dialog = env.open(lang_code_checks=checks)
    _button(dialog, "確定執行").on_click(None)
    assert dialog.open is False
    assert env.snacks == []
    assert env.runs == [
        (
            str(env.src),
            str(env.out),
            "folder",
            True,
            True,
            False,
            0.5,
            2,
            ["zh_tw", "ja_jp"],
        )
    ]


def test_start_strips_paths_and_passes_modified_options(env):
    dialog = env.open()
    _find(dialog, ft.TextField, "Mod 來源").value = f"  {env.src}  "
    _find(dialog, ft.TextField, "輸出目錄").value = f" {env.out} "
    _find(dialog, ft.Checkbox).value = False
    _find(dialog, ft.Switch, "允許 zh_cn 觸發跳過 en_us").value = True
    for c in _walk(dialog):
        if isinstance(c, ft.TextField) and c.width == 100:
            c.value = "0.75"
        elif isinstance(c, ft.TextField) and c.width == 80:
            c.value = "7"
    _button(dialog, "確定執行").on_click(None)
    assert env.runs == [
        (str(env.src), str(env.out), "folder", False, True, True, 0.75, 7, ["zh_tw"])
    ]


@pytest.mark.parametrize("text", ["", "abc"])
def test_patchouli_threshold_falls_back_to_default(env, text):
    dialog = env.open()
    for c in _walk(dialog):
        if isinstance(c, ft.TextField) and c.width == 100:
            c.value = text
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0][6] == 0.5


@pytest.mark.parametrize("text", ["", "x", "1.5"])
def test_zh_en_threshold_falls_back_to_default(env, text):
    dialog = env.open()
    for c in _walk(dialog):
        if isinstance(c, ft.TextField) and c.width == 80:
            c.value = text
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0][7] == 2


def test_zero_thresholds_are_valid_values_not_defaults(env):
    """0 是合法閾值（原本被 `or` 當成空值而換成預設 0.5／2）。"""
    dialog = env.open()
    for c in _walk(dialog):
        if isinstance(c, ft.TextField) and c.width == 100:
            c.value = "0.0"
        elif isinstance(c, ft.TextField) and c.width == 80:
            c.value = "0"
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0][6] == 0.0
    assert env.runs[0][7] == 0


def test_custom_safe_converters_are_used(env):
    dialog = env.open(safe_int=lambda s: 9, safe_float=lambda s: 0.25)
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0][6] == 0.25 and env.runs[0][7] == 9


def test_zh_cn_off_is_passed_with_skip_false(env):
    env.cfg = {"lang_merger": {"patchouli_skip_en_us_when_zh_cn_exists": True}}
    dialog = env.open()
    zh = _find(dialog, ft.Switch, "處理 zh_cn 檔案")
    zh.value = False
    zh.on_change(None)
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0][4] is False and env.runs[0][5] is False


def _pick_zips(env, button, paths):
    """讓 file_picker.pick_files() 回傳指定檔案（Flet 1.0 直接回傳清單），並執行點擊。"""

    async def pick_files(**kwargs):
        return [SimpleNamespace(path=p) for p in paths]

    env.picker.pick_files = pick_files
    button = button or next(
        c
        for c in _walk(env.page.overlay[-1])
        if isinstance(c, ft.Button) and c.content == "選擇 ZIP"
    )
    button.on_click(None)
    _drain(env.page)


def test_zip_mode_requires_selection_and_passes_list(env):
    dialog = env.open()
    _set_mode(dialog, "zip")
    _button(dialog, "確定執行").on_click(None)
    assert env.snacks == ["⚠️ 請選擇 ZIP 檔案"]
    assert env.runs == []

    zip_button = next(
        c for c in _walk(dialog) if isinstance(c, ft.Button) and c.content == "選擇 ZIP"
    )
    _pick_zips(env, zip_button, ["/a/x.zip", "/a/y.zip"])
    assert env.snacks[-1] == "選了 2 個 ZIP"
    assert "DEBUG: pick_zip_input called" not in env.snacks
    # 重複選取不會重複加入
    _pick_zips(env, zip_button, ["/a/x.zip"])
    assert env.snacks[-1] == "選了 2 個 ZIP"
    # 取消選取（回傳空）不變動
    _pick_zips(env, zip_button, [])
    assert env.snacks[-1] == "選了 2 個 ZIP"

    _button(dialog, "確定執行").on_click(None)
    assert env.runs == [
        (
            ["/a/x.zip", "/a/y.zip"],
            str(env.out),
            "zip",
            True,
            True,
            False,
            0.5,
            2,
            ["zh_tw"],
        )
    ]
    assert dialog.open is False


def test_zip_list_remove_button_drops_entry(env):
    dialog = env.open()
    _set_mode(dialog, "zip")
    _pick_zips(env, None, ["/a/x.zip", "/a/y.zip"])
    lv = next(
        c for c in _walk(dialog) if isinstance(c, ft.ListView) and c.height == 100
    )
    assert len(lv.controls) == 2
    names = [r.controls[0].value for r in lv.controls]
    assert names == ["x.zip", "y.zip"]
    lv.controls[0].controls[1].on_click(None)
    assert [r.controls[0].value for r in lv.controls] == ["y.zip"]
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0][0] == ["/a/y.zip"]


def test_pick_folder_buttons_fill_fields_from_picker(env, tmp_path):
    dialog = env.open()
    picked = tmp_path / "picked"
    env.picker.set_mock_path(str(picked))
    buttons = [
        c
        for c in _walk(dialog)
        if isinstance(c, ft.Button) and c.content == "選擇資料夾"
    ]
    assert len(buttons) == 2
    buttons[0].on_click(None)
    _drain(env.page)
    assert _find(dialog, ft.TextField, "Mod 來源").value == str(picked)
    assert _find(dialog, ft.TextField, "輸出目錄").value == str(env.out)
    buttons[1].on_click(None)
    _drain(env.page)
    assert _find(dialog, ft.TextField, "輸出目錄").value == str(picked)


def test_pick_folder_cancelled_keeps_value(env):
    dialog = env.open()
    env.picker.set_mock_path(None)
    next(
        c
        for c in _walk(dialog)
        if isinstance(c, ft.Button) and c.content == "選擇資料夾"
    ).on_click(None)
    _drain(env.page)
    assert _find(dialog, ft.TextField, "Mod 來源").value == str(env.src)


def test_browse_buttons_validate_path(env, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(mod, "open_output_folder", lambda p: opened.append(p) or True)
    dialog = env.open()
    browse = [
        c for c in _walk(dialog) if isinstance(c, ft.Button) and c.content == "瀏覽"
    ]
    src = _find(dialog, ft.TextField, "Mod 來源")
    src.value = ""
    browse[0].on_click(None)
    src.value = str(tmp_path / "nope")
    browse[0].on_click(None)
    assert env.snacks == ["⚠️ 請先選擇資料夾", "⚠️ 路徑不存在"]
    src.value = str(env.src)
    browse[0].on_click(None)
    browse[1].on_click(None)
    assert opened == [str(env.src), str(env.out)]


def test_cancel_closes_without_running_and_reopen_gives_new_dialog(env):
    dialog = env.open()
    _button(dialog, "取消").on_click(None)
    assert dialog.open is False
    assert env.runs == []
    again = env.open()
    assert again is not dialog and again.open is True
    # 關閉後會從 overlay 移除，不會一直累積已關閉的對話框
    assert env.page.overlay == [again]


@pytest.mark.parametrize(
    ("src", "out", "message"),
    [
        ("", "x", "⚠️ 請填寫輸入來源"),
        ("x", "", "⚠️ 請填寫輸出目錄"),
    ],
)
def test_preview_validates_fields(env, src, out, message):
    dialog = env.open()
    _find(dialog, ft.TextField, "Mod 來源").value = src
    _find(dialog, ft.TextField, "輸出目錄").value = out
    _button(dialog, "預覽結果").on_click(None)
    assert env.snacks == [message]
    assert dialog.open is True


def test_preview_is_stub_that_keeps_dialog_open(env):
    """預覽尚未實作：只提示，不關閉對話框（避免丟掉使用者已填的設定）。"""
    dialog = env.open()
    _button(dialog, "預覽結果").on_click(None)
    assert env.snacks == ["🔍 預覽功能待實作"]
    assert dialog.open is True
    assert env.runs == []


def test_preview_in_zip_mode_needs_selected_zip(env):
    dialog = env.open()
    _set_mode(dialog, "zip")
    _button(dialog, "預覽結果").on_click(None)
    assert env.snacks == ["⚠️ 請填寫輸入來源"]
