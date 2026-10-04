"""open_extract_dialog（流水線 Step 1）的行為層測試。

透過公開入口 ``open_extract_dialog`` 操作按鈕與欄位，驗證驗證訊息、模式選擇、
傳給 on_run_extraction 的參數、預覽流程（背景 worker + poller）與重新開啟時讀取最新設定。
背景執行緒以 stub 取代，page.run_task 排入的 coroutine 由測試手動執行。
"""

from __future__ import annotations

import asyncio

import flet as ft
import pytest

from app.views.pipeline import pipeline_extract_dialog as mod
from tests.conftest import _make_page, mock_filepicker


def _walk(control):
    yield control
    for attr in ("controls", "actions"):
        for child in getattr(control, attr, None) or []:
            yield from _walk(child)
    for attr in ("content", "title"):
        child = getattr(control, attr, None)
        if child is not None and not isinstance(child, str):
            yield from _walk(child)


def _texts(control) -> list[str]:
    return [
        c.value
        for c in _walk(control)
        if isinstance(c, ft.Text) and isinstance(c.value, str)
    ]


def _button(dialog, label):
    for a in dialog.actions:
        if getattr(a, "content", None) == label:
            return a
    raise AssertionError(f"找不到按鈕 {label}")


def _find(dialog, cls, **attrs):
    for c in _walk(dialog):
        if isinstance(c, cls) and all(
            getattr(c, k, None) == v for k, v in attrs.items()
        ):
            return c
    raise AssertionError(f"找不到 {cls.__name__} {attrs}")


def _drain(page):
    while page._tasks:
        tasks, page._tasks = page._tasks, []
        for coro, args in tasks:
            asyncio.run(coro(*args))


class _Env:
    def __init__(self, monkeypatch, tmp_path):
        self.page = _make_page(width=1000, height=800)
        self.mods = tmp_path / "mods"
        self.mods.mkdir()
        self.out = tmp_path / "out"
        self.out.mkdir()
        self.snacks: list[str] = []
        self.runs: list[tuple] = []
        self.checks: dict = {}
        self.threads: list = []
        self.updates: list[dict] = []
        self.gen_calls: list[tuple] = []
        self.cfg: dict = {}
        self.picker = mock_filepicker()
        env = self

        class _Thread:
            def __init__(self, target=None, daemon=None, **_):
                self.target = target
                self.daemon = daemon
                env.threads.append(self)

            def start(self):
                pass

        def gen(mods, mode, lang_codes=None):
            env.gen_calls.append((mods, mode, lang_codes))
            return iter(env.updates)

        monkeypatch.setattr(mod.threading, "Thread", _Thread)
        monkeypatch.setattr(mod, "load_config", lambda: env.cfg)
        monkeypatch.setattr(mod, "find_jar_files", lambda d: ["a.jar", "b.jar"])
        monkeypatch.setattr(mod, "preview_extraction_generator", gen)

    def open(self, input_path=None, output_path=None):
        mod.open_extract_dialog(
            self.page,
            self.picker,
            input_path=str(self.mods) if input_path is None else input_path,
            output_path=str(self.out) if output_path is None else output_path,
            on_run_extraction=lambda *a, **k: self.runs.append((a, k)),
            lang_code_checks=self.checks,
            show_snack_bar=lambda msg, *a, **k: self.snacks.append(msg),
        )
        return self.page.overlay[-1]

    def preview(self, dialog):
        _button(dialog, "預覽結果").on_click(None)
        self.threads[-1].target()
        pd = self.page.overlay[-1]
        _drain(self.page)
        return pd


@pytest.fixture
def env(monkeypatch, tmp_path):
    return _Env(monkeypatch, tmp_path)


def _result(**kw):
    base = {
        "total_files": 5,
        "total_size_mb": 1.5,
        "preview_results": [
            {"jar": "a.jar", "count": 5},
            {"jar": "b.jar", "count": 0},
        ],
    }
    base.update(kw)
    return base


# ---------- 初始狀態 ----------


def test_open_shows_modal_dialog_with_prefilled_state(env):
    dialog = env.open()
    assert dialog.open is True and dialog.modal is True
    assert dialog in env.page.overlay
    assert env.page.updated >= 1
    assert dialog.content.width == 600  # page.width * 0.6
    assert _find(dialog, ft.TextField, label="Mod 來源").value == str(env.mods)
    assert _find(dialog, ft.TextField, label="輸出目錄").value == str(env.out)
    assert _find(dialog, ft.RadioGroup).value == "lang"
    assert [a.content for a in dialog.actions] == ["取消", "預覽結果", "確定執行"]


def test_empty_paths_show_fallback_hints(env):
    dialog = env.open(input_path="", output_path="")
    mods = _find(dialog, ft.TextField, label="Mod 來源")
    out = _find(dialog, ft.TextField, label="輸出目錄")
    assert mods.value == "" and out.value == ""
    assert mods.hint_text == "留空使用上方設定的 Mod 來源"
    assert out.hint_text == "留空使用上方設定的輸出目錄"


def test_language_checkboxes_default_to_config_codes_all_checked(env):
    env.cfg = {"jar_extractor": {"lang_codes": ["ja_jp", "ko_kr"]}}
    dialog = env.open()
    boxes = [c for c in _walk(dialog) if isinstance(c, ft.Checkbox)]
    assert [(b.label, b.value) for b in boxes] == [("ja_jp", True), ("ko_kr", True)]


def test_language_checkboxes_fall_back_to_builtin_defaults(env):
    env.cfg = {}
    dialog = env.open()
    labels = [c.label for c in _walk(dialog) if isinstance(c, ft.Checkbox)]
    assert labels == ["en_us", "zh_cn", "zh_tw"]


def test_reopen_reads_fresh_config(env):
    env.cfg = {"jar_extractor": {"lang_codes": ["en_us"]}}
    env.open()
    env.cfg = {"jar_extractor": {"lang_codes": ["fr_fr", "de_de"]}}
    second = env.open()
    labels = [c.label for c in _walk(second) if isinstance(c, ft.Checkbox)]
    assert labels == ["fr_fr", "de_de"]
    assert len(env.page.overlay) == 2


# ---------- 驗證訊息 ----------


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda e: {"input_path": ""}, "⚠️ Mod 來源為必填欄位"),
        (lambda e: {"input_path": str(e.mods / "nope")}, "⚠️ Mod 來源資料夾不存在"),
        (lambda e: {"output_path": ""}, "⚠️ 輸出目錄為必填欄位"),
        (lambda e: {"output_path": str(e.out / "nope")}, "⚠️ 輸出目錄不存在"),
    ],
)
def test_confirm_validation_blocks_run_and_keeps_dialog_open(env, setup, message):
    dialog = env.open(**setup(env))
    _button(dialog, "確定執行").on_click(None)
    assert env.snacks == [message]
    assert env.runs == []
    assert dialog.open is True
    assert env.checks == {}


def test_whitespace_only_mods_is_treated_as_missing(env):
    dialog = env.open(input_path="   ")
    _button(dialog, "確定執行").on_click(None)
    assert env.snacks == ["⚠️ Mod 來源為必填欄位"]


# ---------- 確定執行 ----------


def test_confirm_closes_dialog_and_calls_back_with_exact_args(env):
    dialog = env.open()
    _button(dialog, "確定執行").on_click(None)
    assert dialog.open is False
    assert env.runs == [
        (
            (str(env.mods), str(env.out), "lang"),
            {"lang_codes": ["en_us", "zh_cn", "zh_tw"]},
        )
    ]
    assert env.snacks == []


def test_confirm_strips_whitespace_in_paths(env):
    dialog = env.open(input_path=f"  {env.mods}  ", output_path=f" {env.out} ")
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0][0][:2] == (str(env.mods), str(env.out))


@pytest.mark.parametrize(
    ("radio", "expected"), [("lang", "lang"), ("book", "book"), ("both", "dual")]
)
def test_mode_selection_maps_to_callback_mode(env, radio, expected):
    dialog = env.open()
    _find(dialog, ft.RadioGroup).value = radio
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0][0][2] == expected


def test_unchecked_languages_are_excluded_and_checks_shared_with_caller(env):
    dialog = env.open()
    boxes = {c.label: c for c in _walk(dialog) if isinstance(c, ft.Checkbox)}
    boxes["zh_cn"].value = False
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0][1] == {"lang_codes": ["en_us", "zh_tw"]}
    # 外層持有的 dict 會拿到對話框內的 Checkbox（含取消勾選狀態）
    assert set(env.checks) == {"en_us", "zh_cn", "zh_tw"}
    assert env.checks["zh_cn"] is boxes["zh_cn"] and env.checks["zh_cn"].value is False


def test_all_languages_unchecked_still_runs_with_empty_codes(env):
    dialog = env.open()
    for c in _walk(dialog):
        if isinstance(c, ft.Checkbox):
            c.value = False
    _button(dialog, "確定執行").on_click(None)
    assert env.runs[0][1] == {"lang_codes": []}


def test_cancel_closes_without_callback(env):
    dialog = env.open()
    _button(dialog, "取消").on_click(None)
    assert dialog.open is False
    assert env.runs == [] and env.checks == {}


# ---------- 目錄選擇 / 瀏覽 ----------


def test_pick_dirs_fill_fields_from_picker(env):
    dialog = env.open()
    env.picker.set_mock_path("/picked/dir")
    buttons = [
        c
        for c in _walk(dialog)
        if isinstance(c, ft.Button) and c.content == "選擇資料夾"
    ]
    assert len(buttons) == 2
    buttons[0].on_click(None)
    _drain(env.page)
    assert _find(dialog, ft.TextField, label="Mod 來源").value == "/picked/dir"
    env.picker.set_mock_path("/other")
    buttons[1].on_click(None)
    _drain(env.page)
    assert _find(dialog, ft.TextField, label="輸出目錄").value == "/other"


def test_pick_cancelled_keeps_existing_value(env):
    dialog = env.open()
    env.picker.set_mock_path(None)
    btn = next(
        c
        for c in _walk(dialog)
        if isinstance(c, ft.Button) and c.content == "選擇資料夾"
    )
    btn.on_click(None)
    _drain(env.page)
    assert _find(dialog, ft.TextField, label="Mod 來源").value == str(env.mods)


def test_browse_opens_existing_dir_and_reports_bad_paths(env, monkeypatch):
    opened = []
    monkeypatch.setattr(mod.os, "startfile", opened.append, raising=False)
    dialog = env.open()
    browse = [
        c for c in _walk(dialog) if isinstance(c, ft.Button) and c.content == "瀏覽"
    ]
    browse[0].on_click(None)
    assert opened == [str(env.mods)]

    mods = _find(dialog, ft.TextField, label="Mod 來源")
    mods.value = ""
    browse[0].on_click(None)
    mods.value = str(env.mods / "missing")
    browse[0].on_click(None)
    assert env.snacks == ["⚠️ 請先選擇資料夾", "⚠️ 路徑不存在"]
    assert opened == [str(env.mods)]


# ---------- 預覽流程 ----------


def test_preview_without_valid_mods_shows_snack_and_no_dialog(env):
    dialog = env.open(input_path=str(env.mods / "missing"))
    _button(dialog, "預覽結果").on_click(None)
    assert env.snacks == ["⚠️ 請選擇有效的 Mod 來源"]
    assert env.threads == [] and env.page.overlay == [dialog]


def test_preview_start_shows_scanning_dialog_and_starts_daemon_worker(env):
    dialog = env.open()
    _button(dialog, "預覽結果").on_click(None)
    pd = env.page.overlay[-1]
    assert pd is not dialog and pd.open is True and pd.modal is True
    assert "預覽掃描中...（0/2）" in _texts(pd)
    assert [a.content for a in pd.actions] == ["取消"]
    assert len(env.threads) == 1 and env.threads[0].daemon is True
    assert len(env.page._tasks) == 1  # poller
    assert dialog.open is True  # 主對話框仍保持開啟


def test_preview_passes_mode_and_selected_languages_to_scanner(env):
    env.updates = [{"result": _result()}]
    dialog = env.open()
    _find(dialog, ft.RadioGroup).value = "book"
    next(
        c for c in _walk(dialog) if isinstance(c, ft.Checkbox) and c.label == "en_us"
    ).value = False
    env.preview(dialog)
    assert env.gen_calls == [(str(env.mods), "book", ["zh_cn", "zh_tw"])]


def test_preview_both_mode_is_scanned_as_dual(env):
    env.updates = [{"result": _result()}]
    dialog = env.open()
    _find(dialog, ft.RadioGroup).value = "both"
    env.preview(dialog)
    assert env.gen_calls[0][1] == "dual"


def test_preview_result_lists_only_non_empty_jars(env):
    env.updates = [
        {"progress": 0.5, "current": 1, "total": 2},
        {"progress": 1.0, "current": 2, "total": 2, "result": _result()},
    ]
    dialog = env.open()
    pd = env.preview(dialog)
    texts = _texts(pd.content)
    assert "JAR 數量：2 個" in texts
    assert "預計提取：5 個檔案（約 1.5 MB）" in texts
    assert "  a.jar（5 個檔案）" in texts
    assert not any("b.jar" in t for t in texts)
    assert "  另有 1 個 JAR 沒有可提取的檔案，已略過不列出" in texts
    assert not any("預覽掃描中" in t for t in texts)
    assert [a.content for a in pd.actions] == ["確定"]


def test_preview_dual_result_shows_lang_and_book_counts(env):
    env.updates = [
        {
            "result": {
                "total_files": 7,
                "total_size_mb": 2,
                "preview_results": [
                    {"jar": "m.jar", "lang_count": 3, "book_count": 4},
                    {"jar": "z.jar", "lang_count": 0, "book_count": 0},
                ],
            }
        }
    ]
    dialog = env.open()
    _find(dialog, ft.RadioGroup).value = "both"
    texts = _texts(env.preview(dialog).content)
    assert "  m.jar（Lang: 3, Book: 4）" in texts
    assert not any("z.jar" in t for t in texts)
    assert "  另有 1 個 JAR 沒有可提取的檔案，已略過不列出" in texts


def test_preview_with_no_result_shows_zero_summary(env):
    env.updates = []
    dialog = env.open()
    texts = _texts(env.preview(dialog).content)
    assert "預計提取：0 個檔案（約 0.0 MB）" in texts


@pytest.mark.parametrize("failure", ["generator_error", "exception"])
def test_preview_error_is_rendered_in_dialog(env, monkeypatch, failure):
    if failure == "exception":

        def boom(*a, **k):
            raise RuntimeError("壞掉了")
            yield  # pragma: no cover

        monkeypatch.setattr(mod, "preview_extraction_generator", boom)
        message = "壞掉了"
    else:
        env.updates = [{"error": "讀取失敗"}, {"result": _result()}]
        message = "讀取失敗"
    dialog = env.open()
    pd = env.preview(dialog)
    assert _texts(pd.content) == [f"❌ 錯誤：{message}"]
    assert [a.content for a in pd.actions] == ["確定"]


def test_poller_shows_progress_text_until_worker_done(env, monkeypatch):
    env.updates = [{"result": _result()}]
    states = []

    class _Spy(mod.PreviewState):
        def __init__(self):
            super().__init__()
            states.append(self)

    monkeypatch.setattr(mod, "PreviewState", _Spy)
    dialog = env.open()
    _button(dialog, "預覽結果").on_click(None)
    pd = env.page.overlay[-1]
    snapshots = []
    rounds = []

    async def fake_sleep(_):
        rounds.append(1)
        if len(rounds) == 1:
            states[0].progress, states[0].current, states[0].total = 0.5, 1, 2
        else:
            snapshots.append(_texts(pd.content))
            env.threads[-1].target()

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    _drain(env.page)
    assert snapshots == [["預覽掃描中...（1/2）50%"]]


def test_preview_confirm_button_closes_only_preview_dialog(env):
    env.updates = [{"result": _result()}]
    dialog = env.open()
    pd = env.preview(dialog)
    pd.actions[0].on_click(None)
    assert pd.open is False
    assert dialog.open is True
    assert env.runs == []


def test_preview_cancel_while_scanning_closes_preview_dialog(env):
    dialog = env.open()
    _button(dialog, "預覽結果").on_click(None)
    pd = env.page.overlay[-1]
    pd.actions[0].on_click(None)
    assert pd.open is False and dialog.open is True


def test_preview_can_be_run_twice_independently(env):
    env.updates = [{"result": _result(total_files=1)}]
    dialog = env.open()
    first = env.preview(dialog)
    env.updates = [{"error": "第二次失敗"}]
    second = env.preview(dialog)
    assert first is not second
    assert any("預計提取：1 個檔案" in t for t in _texts(first.content))
    assert _texts(second.content) == ["❌ 錯誤：第二次失敗"]
