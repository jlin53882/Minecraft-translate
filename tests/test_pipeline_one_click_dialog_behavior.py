"""open_one_click_dialog 的行為層測試。

透過公開入口 ``open_one_click_dialog`` 開啟 4 步驟 wizard，走訪控件樹、驅動
on_click / on_change 處理器，驗證導覽、收集到 config 的值與最後的 on_execute 回呼。
語言代碼 Checkbox、步驟 2 的 Switch／閾值欄位、步驟 4 的檔案敘述／ZIP 路徑原本沒有接上
on_change（使用者的修改不會進 config）；現在都會收集，並有對應測試。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import flet as ft
import pytest

from app.views.pipeline import pipeline_one_click_dialog as mod
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


def _drain(page) -> None:
    for _ in range(5):
        tasks, page._tasks = page._tasks, []
        if not tasks:
            return
        for coro, args in tasks:
            result = coro(*args)
            if result is not None:
                try:
                    result.send(None)
                except StopIteration:
                    pass


class _Env:
    def __init__(self, monkeypatch, tmp_path, cfg=None):
        self.page = _make_page(width=1000, height=800)
        self.picker = mock_filepicker()
        self.mods = tmp_path / "mods"
        self.mods.mkdir(exist_ok=True)
        self.out = tmp_path / "out"
        self.executed: list[dict] = []
        self.snacks: list = []
        self.cfg = cfg if cfg is not None else {}
        monkeypatch.setattr(mod, "load_config", lambda: self.cfg)
        monkeypatch.setattr(
            mod, "_load_version_data", lambda: {"1.20": {}, "1.21": {}, "1.19": {}}
        )

    def open(self, **kwargs):
        kwargs.setdefault("input_path", str(self.mods))
        kwargs.setdefault("output_path", str(self.out))
        mod.open_one_click_dialog(
            self.page,
            self.picker,
            on_execute=self.executed.append,
            show_snack_bar=lambda *a: self.snacks.append(a),
            **kwargs,
        )
        return self

    @property
    def dialog(self):
        assert len(self.page.overlay) == 1, "同一時間只應有一個步驟對話框"
        return self.page.overlay[0]

    def controls(self, cls):
        return [c for c in _walk(self.dialog) if isinstance(c, cls)]

    def texts(self):
        return [c.value for c in _walk(self.dialog) if isinstance(c, ft.Text)]

    def button(self, label):
        for a in self.dialog.actions:
            if getattr(a, "content", None) == label:
                return a
        raise AssertionError(f"找不到按鈕 {label}")

    def labels(self):
        return [getattr(a, "content", None) for a in self.dialog.actions]

    def click(self, label):
        self.button(label).on_click(None)

    def goto(self, step):
        for _ in range(step - 1):
            self.click("下一個")

    def title(self):
        return self.dialog.title.controls[0].value

    def step_text(self):
        return self.dialog.title.controls[1].content.value

    def field(self, label):
        for c in self.controls(ft.TextField):
            if c.label == label:
                return c
        raise AssertionError(f"找不到欄位 {label}")

    def switch(self, label):
        for c in self.controls(ft.Switch):
            if c.label == label:
                return c
        raise AssertionError(f"找不到開關 {label}")

    def set_switch(self, label, value):
        sw = self.switch(label)
        sw.value = value
        sw.on_change(SimpleNamespace(control=sw))

    def run_to_end(self):
        self.goto(4)
        return self.confirm()

    def run_to_end_from(self, step):
        for _ in range(4 - step):
            self.click("下一個")
        return self.confirm()

    def confirm(self):
        self.click("確定執行")
        assert len(self.executed) == 1
        return self.executed[0]


@pytest.fixture
def env(monkeypatch, tmp_path):
    return _Env(monkeypatch, tmp_path)


# ---------- 初始狀態 ----------


def test_open_shows_step1_only_with_next_and_cancel(env):
    env.open()
    assert env.dialog.open is True and env.dialog.modal is True
    assert env.title() == "📦 抽取資源設定"
    assert env.step_text() == "1/4"
    assert env.labels() == ["下一個", "取消"]
    assert env.executed == []


def test_step1_shows_readonly_paths_and_default_langs(env):
    env.open()
    texts = env.texts()
    assert str(env.mods) in texts and str(env.out) in texts
    assert [c.label for c in env.controls(ft.Checkbox)] == ["en_us", "zh_cn", "zh_tw"]
    assert all(c.value is True for c in env.controls(ft.Checkbox))
    group = env.controls(ft.RadioGroup)[0]
    assert group.value == "lang"
    assert [r.value for r in env.controls(ft.Radio)] == ["lang", "book", "both"]


def test_empty_paths_show_unset_placeholder(env):
    env.open(input_path="", output_path="")
    assert env.texts().count("未設定") == 2


def test_dialog_width_is_sixty_percent_of_page(env):
    env.open()
    assert env.dialog.content.width == 600


# ---------- 導覽 ----------


def test_next_walks_through_all_four_steps(env):
    env.open()
    seen = []
    for _ in range(3):
        env.click("下一個")
        seen.append((env.title(), env.step_text(), env.labels()))
    assert seen == [
        ("🔍 語系比對設定", "2/4", ["上一個", "下一個", "取消"]),
        ("🔄 啟動翻譯設定", "3/4", ["上一個", "下一個", "取消"]),
        ("📦 打包資源設定", "4/4", ["上一個", "確定執行", "取消"]),
    ]
    assert len(env.page.overlay) == 1 and env.page.overlay[0].open is True


def test_prev_goes_back_and_old_dialog_is_closed(env):
    env.open()
    first = env.dialog
    env.click("下一個")
    second = env.dialog
    assert first.open is False and first not in env.page.overlay
    env.click("上一個")
    assert env.title() == "📦 抽取資源設定"
    assert second.open is False
    assert env.step_text() == "1/4"


def test_navigation_triggers_page_update(env):
    env.open()
    before = env.page.updated
    env.click("下一個")
    assert env.page.updated > before


def test_stale_buttons_do_not_overrun_step_bounds(env):
    env.open()
    stale_next = env.button("下一個")
    env.goto(4)
    stale_next.on_click(None)  # step 4 時再按舊的「下一個」
    assert env.step_text() == "4/4" and env.title() == "📦 打包資源設定"
    for _ in range(3):
        stale_prev = env.button("上一個")
        stale_prev.on_click(None)
    assert env.step_text() == "1/4"
    stale_prev.on_click(None)  # step 1 時再按舊的「上一個」
    assert env.step_text() == "1/4"


def test_no_validation_empty_paths_still_navigate_and_execute(env):
    """目前沒有任何驗證訊息：缺少路徑也能一路走到完成。"""
    env.open(input_path="", output_path="")
    config = env.run_to_end()
    assert config["merge_input"] == ""
    assert config["zip_output"] == ""
    assert env.snacks == []


# ---------- 步驟 1：模式與語言 ----------


@pytest.mark.parametrize("mode", ["book", "both", "lang"])
def test_mode_selection_is_collected_and_survives_navigation(env, mode):
    env.open()
    group = env.controls(ft.RadioGroup)[0]
    group.value = mode
    group.on_change(SimpleNamespace(control=group))
    env.click("下一個")
    env.click("上一個")
    assert env.controls(ft.RadioGroup)[0].value == mode
    # UI 的「全部執行」(both) 在邊界正規化為引擎的 dual
    assert env.run_to_end()["mode"] == ("dual" if mode == "both" else mode)


def test_lang_checkbox_changes_reach_config(env):
    """取消勾選語言代碼會反映在 lang_codes（原本 Checkbox 沒掛 on_change）。"""
    env.open()
    cb = env.controls(ft.Checkbox)[1]
    assert cb.on_change is not None
    cb.value = False
    cb.on_change(SimpleNamespace(control=cb))
    env.click("下一個")
    env.click("上一個")
    assert env.controls(ft.Checkbox)[1].value is False  # 返回步驟 1 仍保留
    config = env.run_to_end_from(1)
    assert config["lang_codes"] == ["en_us", "zh_tw"]


def test_lang_codes_come_from_config_and_zero_codes_means_empty_list(
    monkeypatch, tmp_path
):
    e = _Env(monkeypatch, tmp_path, {"jar_extractor": {"lang_codes": ["ja_jp"]}})
    e.open()
    assert [c.label for c in e.controls(ft.Checkbox)] == ["ja_jp"]
    assert e.run_to_end()["lang_codes"] == ["ja_jp"]

    e2 = _Env(monkeypatch, tmp_path, {"jar_extractor": {"lang_codes": []}})
    e2.open()
    assert e2.controls(ft.Checkbox) == []
    cfg = e2.run_to_end()
    assert cfg["lang_codes"] == []
    assert e2.snacks == []


# ---------- 步驟 2 ----------


def test_step2_defaults(env):
    env.open()
    env.goto(2)
    assert env.switch("只處理 lang 檔案").value is True
    assert env.switch("處理 zh_cn 檔案").value is True
    assert env.switch("允許 zh_cn 觸發跳過 en_us").value is False
    thresholds = [c.value for c in env.controls(ft.TextField) if c.width in (80, 100)]
    assert thresholds == ["2", "0.5"]


def test_step2_has_no_dead_source_controls(env):
    """一鍵流程的語系比對固定處理步驟 1 的提取輸出；原本的「資料夾／ZIP」來源選項從未生效，已移除。"""
    env.open()
    env.goto(2)
    assert env.controls(ft.RadioGroup) == []
    assert env.controls(ft.Radio) == []
    assert not any(f.label == "Mod 來源" for f in env.controls(ft.TextField))
    assert "步驟 1 的提取輸出（自動帶入）" in [
        getattr(c, "value", None) for c in env.controls(ft.Text)
    ]


def test_patchouli_skip_switch_is_collected(env):
    env.open()
    env.goto(2)
    env.set_switch("允許 zh_cn 觸發跳過 en_us", True)
    env.click("上一個")
    env.click("下一個")
    assert env.switch("允許 zh_cn 觸發跳過 en_us").value is True
    assert env.run_to_end_from(2)["patchouli_skip"] is True


def test_step2_switches_are_collected(env):
    """「只處理 lang」「處理 zh_cn」Switch 的變更會進 config（原本沒掛 on_change）；
    兩者與 Patchouli 選項互不相依。"""
    env.open()
    env.goto(2)
    only = env.switch("只處理 lang 檔案")
    zh = env.switch("處理 zh_cn 檔案")
    only.value = False
    only.on_change(SimpleNamespace(control=only))
    zh.value = False
    zh.on_change(SimpleNamespace(control=zh))
    assert env.switch("允許 zh_cn 觸發跳過 en_us").disabled in (None, False)
    for f in env.controls(ft.TextField):
        if f.width in (80, 100):
            assert not f.disabled
    config = env.run_to_end_from(2)
    assert config["process_zh_cn"] is False and config["only_lang"] is False


def _type(field, text):
    field.value = text
    field.on_change(SimpleNamespace(control=field))


def test_threshold_fields_are_collected(env):
    env.open()
    env.goto(2)
    zh_en = next(f for f in env.controls(ft.TextField) if f.width == 80)
    patch = next(f for f in env.controls(ft.TextField) if f.width == 100)
    _type(zh_en, "9")
    _type(patch, "0.9")
    config = env.run_to_end_from(2)
    assert config["zh_en_threshold"] == 9
    assert config["patchouli_threshold"] == 0.9


def test_blank_or_invalid_threshold_falls_back_to_defaults(env):
    """空白／格式錯誤 → 回到設定檔的預設值（欄位提示「空白用預設值」）。"""
    env.open()
    env.goto(2)
    zh_en = next(f for f in env.controls(ft.TextField) if f.width == 80)
    patch = next(f for f in env.controls(ft.TextField) if f.width == 100)
    _type(zh_en, "")
    _type(patch, "abc")
    config = env.run_to_end_from(2)
    assert config["zh_en_threshold"] == 2
    assert config["patchouli_threshold"] == 0.5


def test_step2_defaults_follow_lang_merger_config(monkeypatch, tmp_path):
    e = _Env(
        monkeypatch,
        tmp_path,
        {
            "lang_merger": {
                "patchouli_skip_en_us_when_zh_cn_exists": True,
                "patchouli_effective_translation_threshold": 0.8,
                "zh_en_letter_threshold": 5,
            }
        },
    )
    e.open()
    e.goto(2)
    assert e.switch("允許 zh_cn 觸發跳過 en_us").value is True
    config = e.run_to_end_from(2)
    assert config["patchouli_skip"] is True
    assert config["patchouli_threshold"] == 0.8
    assert config["zh_en_threshold"] == 5


# ---------- 步驟 3 ----------


def test_step3_shows_paths_and_defaults(env):
    env.open()
    env.goto(3)
    texts = env.texts()
    assert os.path.join(str(env.out), "lm_translate", "_翻譯輸出") in texts
    assert (
        os.path.join(
            str(env.out), "locale_sort", "_整理輸出", "lang_output", "待翻譯整理需翻譯"
        )
        in texts
    )
    assert env.switch("Dry Run（只分析不翻譯）").value is False
    assert env.switch("寫入新快取（每次回傳單獨快取）").value is True


def test_step3_switches_are_collected_and_persist(env):
    env.open()
    env.goto(3)
    env.set_switch("Dry Run（只分析不翻譯）", True)
    env.set_switch("寫入新快取（每次回傳單獨快取）", False)
    env.click("上一個")
    env.click("下一個")
    assert env.switch("Dry Run（只分析不翻譯）").value is True
    assert env.switch("寫入新快取（每次回傳單獨快取）").value is False
    config = env.run_to_end_from(3)
    assert config["dry_run"] is True and config["write_new_cache"] is False


def test_step3_uses_custom_organized_folder_name(monkeypatch, tmp_path):
    e = _Env(
        monkeypatch,
        tmp_path,
        {"lang_merger": {"pending_organized_folder_name": "自訂待翻"}},
    )
    e.open()
    e.goto(3)
    assert (
        os.path.join(str(e.out), "locale_sort", "_整理輸出", "lang_output", "自訂待翻")
        in e.texts()
    )


# ---------- 步驟 4 ----------


def test_step4_defaults_from_config(monkeypatch, tmp_path):
    e = _Env(
        monkeypatch,
        tmp_path,
        {
            "output_bundler": {"output_zip_name": "pack.zip"},
            "lang_merger": {"lm_translate_folder_name": "_out"},
        },
    )
    e.open()
    e.goto(4)
    assert e.field("輸出 ZIP 檔案").value == os.path.join(str(e.out), "pack.zip")
    assert e.field("輸入來源").value == os.path.join(str(e.out), "lm_translate", "_out")
    assert e.field("封面圖片（可留空）").value == ""
    assert e.field("封面圖片（可留空）").read_only is True
    assert "點擊選擇版本" in e.texts()


def test_step4_default_zip_name_when_config_empty(env):
    env.open()
    env.goto(4)
    assert env.field("輸出 ZIP 檔案").value == os.path.join(
        str(env.out), "可使用翻譯.zip"
    )
    assert env.run_to_end_from(4)["zip_output"] == os.path.join(
        str(env.out), "可使用翻譯.zip"
    )


def test_step4_text_fields_are_collected(env):
    """檔案敘述／ZIP 路徑的輸入會進 config（原本欄位沒有 on_change）。"""
    env.open()
    env.goto(4)
    _type(env.field("檔案敘述"), "§a漢化")
    _type(env.field("輸出 ZIP 檔案"), "/x/y.zip")
    config = env.run_to_end_from(4)
    assert config["description"] == "§a漢化"
    assert config["zip_output"] == "/x/y.zip"


def _version_items(env):
    return [
        c
        for c in env.controls(ft.Container)
        if c.on_click and isinstance(c.content, ft.Text) and c.content.value[0] == "1"
    ]


def test_version_dropdown_toggle_and_select(env):
    env.open()
    env.goto(4)
    dropdown = next(
        c
        for c in env.controls(ft.Container)
        if isinstance(c.content, ft.ListView) and c.visible is False
    )
    toggle = next(
        c
        for c in env.controls(ft.Container)
        if c.on_click and isinstance(c.content, ft.Row)
    )
    toggle.on_click(None)
    assert dropdown.visible is True
    toggle.on_click(None)
    assert dropdown.visible is False
    assert [i.content.value for i in _version_items(env)] == ["1.20", "1.21", "1.19"]
    next(i for i in _version_items(env) if i.content.value == "1.21").on_click(None)
    assert "1.21" in env.texts()
    assert env.run_to_end_from(4)["version"] == "1.21"


def test_selected_version_persists_across_navigation(env):
    env.open()
    env.goto(4)
    _version_items(env)[0].on_click(None)
    env.click("上一個")
    env.click("下一個")
    assert "1.20" in env.texts()
    assert "點擊選擇版本" not in env.texts()


def test_add_extra_folder_via_picker_and_dedupe(env):
    env.open()
    env.goto(4)
    env.picker.set_mock_path(str(env.mods))
    add = next(b for b in env.controls(ft.Button) if b.content == "+ 新增資料夾")
    add.on_click(None)
    _drain(env.page)
    add.on_click(None)  # 重複選同一個資料夾
    _drain(env.page)
    assert "mods" in env.texts()
    assert env.run_to_end_from(4)["extra_folders"] == [str(env.mods)]


def test_add_extra_folder_cancelled_picker_adds_nothing(env):
    env.open()
    env.goto(4)
    add = next(b for b in env.controls(ft.Button) if b.content == "+ 新增資料夾")
    add.on_click(None)
    _drain(env.page)
    assert env.run_to_end_from(4)["extra_folders"] == []


def test_remove_extra_folder(env, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    env.open()
    env.goto(4)
    add = next(b for b in env.controls(ft.Button) if b.content == "+ 新增資料夾")
    for p in (env.mods, other):
        env.picker.set_mock_path(str(p))
        add.on_click(None)
        _drain(env.page)
    close_btns = [
        b
        for b in env.controls(ft.IconButton)
        if b.icon == ft.Icons.CLOSE and b.icon_size == 14
    ]
    assert len(close_btns) == 2
    close_btns[0].on_click(None)
    assert env.run_to_end_from(4)["extra_folders"] == [str(other)]


# ---------- 完成 / 取消 ----------


def test_confirm_calls_on_execute_once_with_default_config(env):
    env.open()
    dialog = env.dialog
    config = env.run_to_end()
    assert config == {
        "mode": "lang",
        "lang_codes": ["en_us", "zh_cn", "zh_tw"],
        "only_lang": True,
        "process_zh_cn": True,
        "patchouli_skip": False,
        "patchouli_threshold": 0.5,
        "zh_en_threshold": 2,
        "dry_run": False,
        "write_new_cache": True,
        "description": "",
        "version": "",
        "min_format": None,
        "max_format": None,
        "pack_image": None,
        "extra_folders": [],
        "zip_output": os.path.join(str(env.out), "可使用翻譯.zip"),
        "merge_input": str(env.mods),
    }
    assert dialog.open is False
    assert env.page.overlay == []


def test_selected_version_maps_to_pack_formats(env, monkeypatch):
    """所選版本要轉成 min/max pack_format 交給打包（原本只傳版本字串、格式一律 0）。"""
    monkeypatch.setattr(
        mod,
        "_load_version_data",
        lambda: {"1.20": {"min_format": 15, "max_format": 18}, "1.21": {}},
    )
    env.open()
    env.goto(4)
    next(i for i in _version_items(env) if i.content.value == "1.20").on_click(None)
    config = env.confirm()
    assert (config["min_format"], config["max_format"]) == (15, 18)


def test_confirm_closes_current_dialog(env):
    env.open()
    env.goto(4)
    dialog = env.dialog
    env.click("確定執行")
    assert dialog.open is False and env.page.overlay == []


@pytest.mark.parametrize("step", [1, 2, 3, 4])
def test_cancel_closes_dialog_without_callback(env, step):
    env.open()
    env.goto(step)
    dialog = env.dialog
    env.click("取消")
    assert dialog.open is False
    assert env.page.overlay == []
    assert env.executed == []


def test_cancel_discards_state_and_reopen_has_fresh_defaults(env):
    env.open()
    env.goto(3)
    env.set_switch("Dry Run（只分析不翻譯）", True)
    env.click("取消")
    env.open()
    env.goto(3)
    assert env.switch("Dry Run（只分析不翻譯）").value is False
    assert env.step_text() == "3/4"
    env.page.overlay.clear()
    env.open()
    assert env.title() == "📦 抽取資源設定"


def test_reopen_reads_updated_config(env):
    env.open()
    assert [c.label for c in env.controls(ft.Checkbox)] == ["en_us", "zh_cn", "zh_tw"]
    env.click("取消")
    env.cfg["jar_extractor"] = {"lang_codes": ["de_de", "fr_fr"]}
    env.cfg["lang_merger"] = {"zh_en_letter_threshold": 7}
    env.open()
    assert [c.label for c in env.controls(ft.Checkbox)] == ["de_de", "fr_fr"]
    assert env.run_to_end()["zh_en_threshold"] == 7


def test_collected_values_flow_into_single_config(env):
    env.open()
    group = env.controls(ft.RadioGroup)[0]
    group.value = "both"
    group.on_change(SimpleNamespace(control=group))
    env.goto(2)
    env.set_switch("允許 zh_cn 觸發跳過 en_us", True)
    env.click("下一個")
    env.set_switch("Dry Run（只分析不翻譯）", True)
    env.click("下一個")
    _version_items(env)[1].on_click(None)
    config = env.run_to_end_from(4)
    assert (config["mode"], config["patchouli_skip"], config["dry_run"]) == (
        "dual",
        True,
        True,
    )
    assert config["version"] == "1.21"
    assert len(env.executed) == 1


def test_closing_sends_open_false_before_removing_from_overlay(env):
    """先把 open=False 送到前端、再移出 overlay。

    直接移除會讓前端的 dialog route 留在畫面上（殘影並擋住整個頁面；網頁版實測重現）。
    """
    env.open()
    dialog = env.dialog
    seen = []
    original = env.page.update

    def spy(*a, **k):
        seen.append((dialog.open, dialog in env.page.overlay))
        return original(*a, **k)

    env.page.update = spy
    env.click("取消")
    assert (False, True) in seen  # 關閉狀態送出時，dialog 還在 overlay 內
    assert env.page.overlay == []


def test_navigation_closes_the_previous_step_dialog_properly(env):
    env.open()
    first = env.dialog
    seen = []
    original = env.page.update

    def spy(*a, **k):
        seen.append((first.open, first in env.page.overlay))
        return original(*a, **k)

    env.page.update = spy
    env.click("下一個")
    assert (False, True) in seen
    assert first not in env.page.overlay


# ---------- 步驟 4：控制項契約（沒有「看得到、改了卻不生效」的欄位） ----------


def test_bundle_input_is_read_only_and_explained(env):
    """一鍵流程的打包來源由流程自動決定（PipelineConfig.bundle_sources），欄位不可編輯。"""
    env.open()
    env.goto(4)
    field = env.field("輸入來源")
    assert field.read_only is True
    assert field.on_change is None
    assert any("不可在此修改" in t for t in env.texts())
    # 改了也不會進 config：config 不含任何 bundle_input 鍵
    config = env.confirm()
    assert "bundle_input" not in config


@pytest.mark.parametrize("step", [1, 2, 3, 4])
def test_no_dead_editable_controls_in_any_step(env, step):
    """每個步驟上「可編輯」的欄位／開關／勾選都必須有 on_change（否則修改不會生效）。"""
    env.open()
    env.goto(step)
    for cls in (ft.TextField, ft.Switch, ft.Checkbox, ft.RadioGroup):
        for control in env.controls(cls):
            if getattr(control, "read_only", False):
                continue
            assert control.on_change is not None, (
                f"步驟 {step} 的 {cls.__name__} {getattr(control, 'label', '')!r} "
                "可編輯但沒有 on_change：使用者的修改不會生效"
            )


def _pick_image(env, path):
    env.picker._mock_path = path
    button = next(
        c
        for c in env.controls(ft.Button)
        if getattr(c, "content", None) == "選擇檔案..."
    )
    button.on_click(None)
    _drain(env.page)


def test_pack_image_picker_sets_state_field_and_config(env):
    env.open()
    env.goto(4)
    _pick_image(env, "/img/cover.PNG")
    assert env.field("封面圖片（可留空）").value == "/img/cover.PNG"
    assert env.confirm()["pack_image"] == "/img/cover.PNG"


def test_pack_image_survives_navigation(env):
    env.open()
    env.goto(4)
    _pick_image(env, "/img/cover.jpg")
    env.click("上一個")
    env.click("下一個")
    assert env.field("封面圖片（可留空）").value == "/img/cover.jpg"
    assert env.confirm()["pack_image"] == "/img/cover.jpg"


def test_pack_image_remove_clears_state_and_config(env):
    env.open()
    env.goto(4)
    _pick_image(env, "/img/cover.png")
    remove = next(
        c for c in env.controls(ft.TextButton) if getattr(c, "content", None) == "移除"
    )
    remove.on_click(None)
    assert env.field("封面圖片（可留空）").value == ""
    assert env.confirm()["pack_image"] is None


def test_pack_image_rejects_unsupported_extension_and_ignores_cancel(env):
    env.open()
    env.goto(4)
    _pick_image(env, "/img/cover.gif")
    assert env.snacks == [("⚠️ 封面圖片只支援 .png/.jpg",)]
    assert env.field("封面圖片（可留空）").value == ""
    _pick_image(env, None)  # 取消選擇
    assert env.field("封面圖片（可留空）").value == ""
    assert env.confirm()["pack_image"] is None
