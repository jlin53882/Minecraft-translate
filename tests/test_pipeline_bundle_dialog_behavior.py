"""open_bundle_dialog（流水線 Step 4）的行為層測試。

透過公開入口操作欄位與按鈕，驗證預設值、驗證訊息、傳給 on_start_bundle 的參數、
封面圖片 / 額外資料夾 / 版本清單互動、預覽按鈕與關閉行為，以及重新開啟時讀取最新設定。
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import flet as ft
import pytest

from app.views.pipeline import pipeline_bundle_dialog as mod
from tests.conftest import _make_page


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


def _field(dialog, label):
    for c in _walk(dialog):
        if isinstance(c, ft.TextField) and c.label == label:
            return c
    raise AssertionError(f"找不到欄位 {label}")


def _inner_button(dialog, label):
    for c in _walk(dialog):
        if isinstance(c, ft.Button) and c.content == label:
            return c
    raise AssertionError(f"找不到按鈕 {label}")


def _drain(page):
    while page._tasks:
        tasks, page._tasks = page._tasks, []
        for coro, args in tasks:
            asyncio.run(coro(*args))


class _Picker:
    """get_directory_path / pick_files / save_file 的可控替身（Flet 1.0：pick_files 直接回傳清單）。"""

    def __init__(self):
        self.dirs: list[str | None] = []
        self.files_path: str | None = None
        self.pick_kwargs: dict = {}
        self.save_path: str | None = None
        self.save_kwargs: dict = {}

    async def save_file(self, **kwargs):
        self.save_kwargs = kwargs
        return self.save_path

    async def get_directory_path(self, dialog_title=None):
        return self.dirs.pop(0) if self.dirs else None

    async def pick_files(self, dialog_title=None, allowed_extensions=None):
        self.pick_kwargs = {
            "dialog_title": dialog_title,
            "allowed_extensions": allowed_extensions,
        }
        if self.files_path is None:
            return []
        return [SimpleNamespace(path=self.files_path)]


class _Env:
    def __init__(self, monkeypatch, tmp_path):
        self.page = _make_page(width=1000, height=800)
        self.out = tmp_path / "out"
        self.out.mkdir()
        self.tmp = tmp_path
        self.snacks: list[str] = []
        self.calls: list[dict] = []
        self.cfg: dict = {}
        self.versions: dict = {"1.20.1": {}, "1.19.4": {}, "1.21": {}}
        self.picker = _Picker()
        monkeypatch.setattr(mod, "load_config", lambda: self.cfg)
        monkeypatch.setattr(mod, "_load_version_data", lambda: self.versions)

    def open(self, input_path="", output_path=None):
        """input_path 是管線頁的 Mod 來源（對話框不使用）；要模擬使用者改輸入來源請用 ``type_input``。"""
        mod.open_bundle_dialog(
            self.page,
            self.picker,
            input_path=input_path,
            output_path=str(self.out) if output_path is None else output_path,
            on_start_bundle=lambda **kw: self.calls.append(kw),
            show_snack_bar=lambda msg, *a, **k: self.snacks.append(msg),
        )
        return self.page.overlay[-1]

    def open_typing_input(self, path):
        """開啟後把「輸入來源」改成 path（模擬使用者輸入）。"""
        dialog = self.open()
        _field(dialog, "輸入來源").value = str(path)
        return dialog


@pytest.fixture
def env(monkeypatch, tmp_path):
    return _Env(monkeypatch, tmp_path)


def _default_input(env, sub="_翻譯輸出"):
    return os.path.join(str(env.out), "lm_translate", sub)


# ---------- 初始狀態 ----------


def test_open_shows_modal_dialog_with_three_actions(env):
    dialog = env.open()
    assert dialog.open is True and dialog.modal is True
    assert dialog in env.page.overlay
    assert dialog.content.width == 600
    assert [a.content for a in dialog.actions] == ["取消", "預覽結果", "確定執行"]


def test_default_input_comes_from_output_path_and_config_subfolder(env):
    env.cfg = {"lang_merger": {"lm_translate_folder_name": "自訂輸出"}}
    dialog = env.open()
    assert _field(dialog, "輸入來源").value == _default_input(env, "自訂輸出")
    assert (
        _field(dialog, "輸入來源").hint_text
        == f"自動帶入：{_default_input(env, '自訂輸出')}"
    )


def test_default_subfolder_and_zip_name_without_config(env):
    dialog = env.open()
    assert _field(dialog, "輸入來源").value == _default_input(env)
    zip_field = _field(dialog, "輸出 ZIP 檔案")
    assert zip_field.value == os.path.join(
        str(env.out), "可使用翻譯.zip"
    )  # 預設直接帶入
    assert (
        zip_field.hint_text
        == f"自動帶入：{os.path.join(str(env.out), '可使用翻譯.zip')}"
    )


def test_mod_source_is_never_used_as_bundle_input(env):
    """原本把 Mod 來源當成打包輸入；預設應是 {output}/lm_translate/_翻譯輸出。"""
    dialog = env.open(input_path="/some/mods")
    assert _field(dialog, "輸入來源").value == _default_input(env)


def test_empty_output_path_gives_empty_defaults_and_fallback_hints(env):
    dialog = env.open(output_path="")
    assert _field(dialog, "輸入來源").value == ""
    assert _field(dialog, "輸入來源").hint_text == "留空自動帶入翻譯完成後的輸出"
    assert _field(dialog, "輸出 ZIP 檔案").hint_text == "留空自動帶入可使用翻譯.zip"


def test_cover_image_field_is_read_only_and_description_empty(env):
    dialog = env.open()
    assert _field(dialog, "封面圖片（可留空）").read_only is True
    assert _field(dialog, "封面圖片（可留空）").value in (None, "")
    assert _field(dialog, "檔案敘述").value in (None, "")


def test_reopen_reads_fresh_config(env):
    first = env.open()
    env.cfg = {
        "output_bundler": {"output_zip_name": "new.zip"},
        "lang_merger": {"lm_translate_folder_name": "新資料夾"},
    }
    second = env.open()
    assert _field(first, "輸入來源").value == _default_input(env)
    assert _field(second, "輸入來源").value == _default_input(env, "新資料夾")
    assert _field(second, "輸出 ZIP 檔案").hint_text.endswith("new.zip")


# ---------- 驗證 ----------


def test_confirm_with_missing_input_dir_is_rejected(env):
    dialog = env.open()  # 預設輸入資料夾尚未建立
    _field(dialog, "輸出 ZIP 檔案").value = str(env.tmp / "x.zip")
    _button(dialog, "確定執行").on_click(None)
    assert "⚠️ 輸入資料夾不存在" in _texts(dialog)
    assert env.snacks == []
    assert env.calls == [] and dialog.open is True


def test_default_zip_path_is_prefilled_and_accepted(env):
    """輸出 ZIP 預設已帶入（原本欄位留空又不允許空白，預設值形同無效）。"""
    os.makedirs(_default_input(env))
    dialog = env.open()
    _button(dialog, "確定執行").on_click(None)
    assert env.snacks == []
    assert env.calls[0]["output_zip_path"] == os.path.join(
        str(env.out), "可使用翻譯.zip"
    )


def test_confirm_with_empty_zip_name_is_rejected(env):
    dialog = env.open_typing_input(env.out)
    _field(dialog, "輸出 ZIP 檔案").value = "   "
    _button(dialog, "確定執行").on_click(None)
    assert "⚠️ 輸出 ZIP 檔名不可空白" in _texts(dialog)
    assert env.snacks == []
    assert env.calls == [] and dialog.open is True


def test_confirm_rejects_existing_image_with_unsupported_extension(env):
    img = env.tmp / "cover.gif"
    img.write_bytes(b"x")
    dialog = env.open_typing_input(env.out)
    _field(dialog, "輸出 ZIP 檔案").value = "a.zip"
    _field(dialog, "封面圖片（可留空）").value = str(img)
    _button(dialog, "確定執行").on_click(None)
    assert "⚠️ 封面圖片只支援 .png/.jpg" in _texts(dialog)
    assert env.snacks == []
    assert env.calls == []


def test_confirm_rejects_nonexistent_image(env):
    """原本驗證反了：不存在的 .png 會通過，存在但副檔名錯的反而被放行。"""
    dialog = env.open_typing_input(env.out)
    _field(dialog, "輸出 ZIP 檔案").value = "a.zip"
    _field(dialog, "封面圖片（可留空）").value = str(env.tmp / "gone.png")
    _button(dialog, "確定執行").on_click(None)
    assert "⚠️ 封面圖片檔案不存在" in _texts(dialog)
    assert env.snacks == []
    assert env.calls == []


def test_existing_png_is_accepted_and_forwarded(env):
    img = env.tmp / "cover.png"
    img.write_bytes(b"x")
    dialog = env.open_typing_input(env.out)
    _field(dialog, "輸出 ZIP 檔案").value = "a.zip"
    _field(dialog, "封面圖片（可留空）").value = str(img)
    _button(dialog, "確定執行").on_click(None)
    assert env.snacks == []
    assert env.calls[0]["pack_image_path"] == str(img)


# ---------- callback 參數 ----------


def test_confirm_calls_back_with_exact_kwargs_and_closes(env):
    dialog = env.open_typing_input(env.out)
    zip_path = str(env.tmp / "pack.zip")
    _field(dialog, "輸出 ZIP 檔案").value = f"  {zip_path} "
    _field(dialog, "檔案敘述").value = "  §a測試翻譯  "
    _button(dialog, "確定執行").on_click(None)
    assert dialog.open is False
    assert env.snacks == []
    assert env.calls == [
        {
            "input_root_dir": str(env.out),
            "output_zip_path": zip_path,
            "description": "§a測試翻譯",
            "min_format": None,
            "max_format": None,
            "pack_image_path": None,
            "extra_folders": [],
        }
    ]


def test_blank_input_falls_back_to_default_input_dir(env):
    default = _default_input(env)
    os.makedirs(default)
    dialog = env.open()
    _field(dialog, "輸入來源").value = ""
    _field(dialog, "輸出 ZIP 檔案").value = "z.zip"
    _button(dialog, "確定執行").on_click(None)
    assert env.calls[0]["input_root_dir"] == default


def test_no_version_selected_passes_no_formats(env):
    dialog = env.open_typing_input(env.out)
    _field(dialog, "輸出 ZIP 檔案").value = "z.zip"
    _button(dialog, "確定執行").on_click(None)
    assert env.calls[0]["min_format"] is None and env.calls[0]["max_format"] is None


def test_selected_version_maps_to_pack_formats(env):
    """所選 Minecraft 版本要轉成 pack_format 範圍（原本一律傳 None）。"""
    env.versions["1.20.1"] = {"min_format": 9, "max_format": 15}
    dialog = env.open_typing_input(env.out)
    _field(dialog, "輸出 ZIP 檔案").value = "z.zip"
    _version_item(dialog, "1.20.1").on_click(None)
    _button(dialog, "確定執行").on_click(None)
    assert env.calls[0]["min_format"] == 9 and env.calls[0]["max_format"] == 15


def test_cancel_closes_without_callback(env):
    dialog = env.open()
    _button(dialog, "取消").on_click(None)
    assert dialog.open is False and env.calls == [] and env.snacks == []


# ---------- 版本清單 ----------


def _version_items(dialog):
    return [
        c
        for c in _walk(dialog)
        if isinstance(c, ft.Container)
        and c.on_click is not None
        and isinstance(c.content, ft.Text)
    ]


def _version_item(dialog, name):
    return next(c for c in _version_items(dialog) if c.content.value == name)


def _toggle(dialog):
    return next(
        c
        for c in _walk(dialog)
        if isinstance(c, ft.Container)
        and isinstance(c.content, ft.Row)
        and any(isinstance(x, ft.Icon) for x in c.content.controls)
    )


def _dropdown(dialog):
    return next(
        c
        for c in _walk(dialog)
        if isinstance(c, ft.Container) and isinstance(c.content, ft.ListView)
    )


def test_version_list_initially_lists_all_versions_and_is_collapsed(env):
    dialog = env.open()
    assert [c.content.value for c in _version_items(dialog)] == [
        "1.20.1",
        "1.19.4",
        "1.21",
    ]
    assert _dropdown(dialog).visible is False


def test_toggle_expands_and_collapses_version_list(env):
    dialog = env.open()
    _toggle(dialog).on_click(None)
    assert _dropdown(dialog).visible is True
    _toggle(dialog).on_click(None)
    assert _dropdown(dialog).visible is False


def test_version_search_filters_case_insensitively(env):
    dialog = env.open()
    search = _field(dialog, "搜尋版本")
    search.on_change(SimpleNamespace(control=SimpleNamespace(value="1.2")))
    assert [c.content.value for c in _version_items(dialog)] == ["1.20.1", "1.21"]


def test_version_search_without_match_shows_placeholder(env):
    dialog = env.open()
    _field(dialog, "搜尋版本").on_change(
        SimpleNamespace(control=SimpleNamespace(value="zzz"))
    )
    assert _version_items(dialog) == []
    assert "無可用版本" in _texts(_dropdown(dialog))


def test_selecting_version_updates_selected_label(env):
    dialog = env.open()
    _toggle(dialog).on_click(None)
    assert _dropdown(dialog).visible is True
    _version_item(dialog, "1.19.4").on_click(None)
    assert _dropdown(dialog).visible is False  # 選完自動收合
    _assert_selected_version_row(dialog, "1.19.4")


def _assert_selected_version_row(dialog, version):
    """用實際組合出的版面驗證：「已選擇：」只出現一次、版本只出現一次（不是重複前綴）。

    版面是 ``Row([Text("已選擇："), Text(version), ...])``；測試找出那一列，
    把列內所有文字串起來檢查。
    """
    row = next(
        c
        for c in _walk(dialog)
        if isinstance(c, ft.Row)
        and any(isinstance(x, ft.Text) and x.value == "已選擇：" for x in c.controls)
    )
    values = [x.value for x in row.controls if isinstance(x, ft.Text)]
    assert values == ["已選擇：", version]
    shown = "".join(values)
    assert shown == f"已選擇：{version}"
    assert shown.count("已選擇：") == 1 and shown.count(version) == 1
    assert "已選擇：已選擇：" not in shown and "已選擇： 已選擇：" not in shown
    # 整個對話框內也不存在重複前綴
    assert not any("已選擇：已選擇" in t for t in _texts(dialog))


def test_missing_version_data_still_opens_with_placeholder(env):
    env.versions = {}
    dialog = env.open()
    assert "無可用版本" in _texts(_dropdown(dialog))


# ---------- 封面圖片 / 額外資料夾 ----------


def test_pick_pack_image_fills_field_and_restricts_extensions(env):
    dialog = env.open()
    env.picker.files_path = "/img/pack.png"
    _inner_button(dialog, "選擇檔案...").on_click(None)
    _drain(env.page)
    assert _field(dialog, "封面圖片（可留空）").value == "/img/pack.png"
    assert env.picker.pick_kwargs["allowed_extensions"] == ["png", "jpg", "jpeg"]


def test_pick_pack_image_cancelled_keeps_value(env):
    dialog = env.open()
    env.picker.files_path = None
    _inner_button(dialog, "選擇檔案...").on_click(None)
    _drain(env.page)
    assert _field(dialog, "封面圖片（可留空）").value in (None, "")


def test_remove_button_clears_pack_image_and_it_is_not_passed(env):
    dialog = env.open_typing_input(env.out)
    _field(dialog, "輸出 ZIP 檔案").value = "z.zip"
    _field(dialog, "封面圖片（可留空）").value = str(env.tmp / "p.png")
    updated = env.page.updated
    _inner_button(dialog, "移除").on_click(None)
    assert _field(dialog, "封面圖片（可留空）").value == ""
    assert env.page.updated == updated + 1
    _button(dialog, "確定執行").on_click(None)
    assert env.calls[0]["pack_image_path"] is None


def test_extra_folders_add_dedupe_remove_and_forwarded(env):
    dialog = env.open_typing_input(env.out)
    _field(dialog, "輸出 ZIP 檔案").value = "z.zip"
    add = _inner_button(dialog, "+ 新增資料夾")
    env.picker.dirs = ["/data/alpha", "/data/alpha", "/data/beta", None]
    for _ in range(4):
        add.on_click(None)
        _drain(env.page)
    names = [t for t in _texts(dialog) if t in ("alpha", "beta")]
    assert names == ["alpha", "beta"]

    remove = [
        c for c in _walk(dialog) if isinstance(c, ft.IconButton) and c.tooltip == "移除"
    ]
    assert len(remove) == 2
    remove[0].on_click(None)
    assert [t for t in _texts(dialog) if t in ("alpha", "beta")] == ["beta"]

    _button(dialog, "確定執行").on_click(None)
    assert env.calls[0]["extra_folders"] == ["/data/beta"]


# ---------- 選擇 / 瀏覽目錄 ----------


def test_pick_input_dir_and_output_zip_fill_fields(env):
    dialog = env.open()
    env.picker.dirs = ["/chosen/in"]
    env.picker.save_path = "/chosen/out/my_pack"
    _inner_button(dialog, "選擇資料夾").on_click(None)
    _drain(env.page)
    _inner_button(dialog, "選擇儲存位置").on_click(None)
    _drain(env.page)
    assert _field(dialog, "輸入來源").value == "/chosen/in"
    # 輸出是 ZIP「檔案」：用另存新檔，並補上 .zip（原本選的是資料夾，後續建立 ZIP 會失敗）
    assert _field(dialog, "輸出 ZIP 檔案").value == "/chosen/out/my_pack.zip"
    assert env.picker.save_kwargs["allowed_extensions"] == ["zip"]


def test_pick_output_zip_cancelled_keeps_value(env):
    dialog = env.open()
    before = _field(dialog, "輸出 ZIP 檔案").value
    env.picker.save_path = None
    _inner_button(dialog, "選擇儲存位置").on_click(None)
    _drain(env.page)
    assert _field(dialog, "輸出 ZIP 檔案").value == before


def test_pick_cancelled_keeps_input_value(env):
    dialog = env.open()
    before = _field(dialog, "輸入來源").value
    env.picker.dirs = [None]
    _inner_button(dialog, "選擇資料夾").on_click(None)
    _drain(env.page)
    assert _field(dialog, "輸入來源").value == before


def test_browse_input_dir_messages_and_open(env, monkeypatch):
    opened = []
    monkeypatch.setattr(mod, "open_output_folder", lambda p: opened.append(p) or True)
    dialog = env.open_typing_input(env.out)
    browse = _inner_button(dialog, "瀏覽")
    browse.on_click(None)
    assert opened == [str(env.out)]
    field = _field(dialog, "輸入來源")
    field.value = ""
    browse.on_click(None)
    field.value = str(env.tmp / "missing")
    browse.on_click(None)
    assert env.snacks == ["⚠️ 請先選擇資料夾", "⚠️ 路徑不存在"]
    assert opened == [str(env.out)]


# ---------- 預覽按鈕 ----------


def test_preview_with_missing_input_dir_shows_error_and_keeps_open(env):
    dialog = env.open()
    _button(dialog, "預覽結果").on_click(None)
    assert "⚠️ 輸入資料夾不存在" in _texts(dialog)
    assert env.snacks == []
    assert dialog.open is True


def test_stale_preview_click_after_close_does_not_start(env):
    dialog = env.open()
    dialog.open = False

    _button(dialog, "預覽結果").on_click(None)

    assert dialog.open is False
    assert env.calls == []


def test_preview_with_no_input_and_no_output_shows_error(env):
    dialog = env.open(output_path="")
    _button(dialog, "預覽結果").on_click(None)
    assert "⚠️ 輸入資料夾不存在" in _texts(dialog)
    assert env.snacks == []
    assert dialog.open is True


def test_preview_with_valid_input_reports_not_implemented_and_keeps_open(env):
    dialog = env.open_typing_input(env.out)
    _button(dialog, "預覽結果").on_click(None)
    assert any("資源打包預覽尚未支援" in text for text in _texts(dialog))
    assert env.snacks == []
    assert dialog.open is True
    assert env.calls == []


def test_preview_falls_back_to_default_input_when_field_blank(env):
    os.makedirs(_default_input(env))
    dialog = env.open()
    _field(dialog, "輸入來源").value = ""
    _button(dialog, "預覽結果").on_click(None)
    assert any("資源打包預覽尚未支援" in text for text in _texts(dialog))
    assert env.snacks == []
    assert dialog.open is True


def test_browse_reports_failure_when_folder_cannot_be_opened(env, monkeypatch):
    """非 Windows 或開啟失敗時顯示提示，而不是在 handler 內丟 AttributeError。"""
    monkeypatch.setattr(mod, "open_output_folder", lambda p: False)
    dialog = env.open_typing_input(env.out)
    _inner_button(dialog, "瀏覽").on_click(None)
    assert env.snacks == ["⚠️ 無法開啟資料夾"]


def test_closing_removes_dialog_from_overlay_after_sending_close(env):
    dialog = env.open()
    seen = []
    original = env.page.update

    def spy(*a, **k):
        seen.append((dialog.open, dialog in env.page.overlay))
        return original(*a, **k)

    env.page.update = spy
    _button(dialog, "取消").on_click(None)
    assert (False, True) in seen  # 先送出 open=False，再移除
    assert dialog not in env.page.overlay
