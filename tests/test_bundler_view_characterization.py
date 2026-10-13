import json

from app.ui.design import C
from app.ui.snack import show_snack
from app.views.bundler_view import BundlerView
from tests.conftest import mock_filepicker, mock_page


def _drain_ui_tasks(page):
    """執行背景 worker 排入 event loop 的 UI 更新（run_task）。"""
    import asyncio

    while page._tasks:
        handler, args = page._tasks.pop(0)
        asyncio.run(handler(*args))


def test_bundler_view_initializes_core_controls():
    view = BundlerView(mock_page(), mock_filepicker())

    assert view.progress_bar.visible is False
    assert hasattr(view, "version_field")
    assert hasattr(view, "max_format_field")
    assert hasattr(view, "description_field")
    assert hasattr(view, "pack_image_field")
    assert hasattr(view, "root_dir_field")
    assert hasattr(view, "output_zip_field")
    assert hasattr(view, "extra_folders_view")


def test_bundler_view_extra_folders_list_initialized():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    assert view.extra_folders == []
    assert len(view.extra_folders_view.controls) == 0


def test_version_input_updates_preview_and_summary():
    view = BundlerView(mock_page(), mock_filepicker())

    view.version_field.value = "1.20.4"
    view._update_preview()
    assert view.preview_format.value == "pack_format 22"
    assert "supported_formats 22–46" in view.version_summary.value

    view.max_format_field.value = "999"
    view._update_preview()
    assert "supported_formats 22–999" in view.version_summary.value
    data = json.loads(view.mcmeta_text())
    assert data["pack"]["supported_formats"] == {
        "min_inclusive": 22,
        "max_inclusive": 999,
    }


def test_invalid_version_input_shows_warning_and_blocks_start():
    view = BundlerView(mock_page(), mock_filepicker())
    view.root_dir_field.value = "C:/Root"
    view.version_field.value = "abc"
    view._update_preview()
    assert view.preview_format.value == "版本或 pack_format 無效"
    assert view.version_summary.value.startswith("⚠️")
    assert "無效" in view.mcmeta_text()
    view.start_bundling_clicked(None)
    assert view._bundling_running is False


def test_max_below_pack_format_is_rejected():
    view = BundlerView(mock_page(), mock_filepicker())
    view.version_field.value = "1.21.1"
    view.max_format_field.value = "10"
    view._update_preview()
    assert view.version_summary.value.startswith("⚠️")


def test_quick_version_button_fills_field():
    view = BundlerView(mock_page(), mock_filepicker())
    view._use_quick_version("1.20.1")
    assert view.version_field.value == "1.20.1"
    assert view.preview_format.value == "pack_format 15"


def test_bundler_view_hint_texts():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    assert "輸入遊戲版本" in view.version_field.hint_text
    assert "最大使用版本" in view.max_format_field.hint_text
    assert view.description_field.hint_text == "直接輸入文字，或使用 § 顏色代碼"
    assert view.pack_image_field.hint_text == "選擇 pack.png 圖片（可選）"
    assert view.root_dir_field.hint_text == "包含所有翻譯產出的最上層資料夾"
    assert "留空則自動帶入" in view.output_zip_field.hint_text


def test_bundler_view_has_config_output_zip_name():
    """測試 _config_output_zip_name 屬性存在且為預設值"""
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    assert hasattr(view, "_config_output_zip_name")
    assert view._config_output_zip_name == "可使用翻譯.zip"


def test_on_root_dir_change_updates_hint_text():
    """測試當 root_dir 改變時，hint_text 會更新"""
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    view._config_output_zip_name = "可使用翻譯.zip"

    view.root_dir_field.value = "C:\\Users\\test\\output"

    class MockEvent:
        def __init__(self):
            self.control = view.root_dir_field

    view._on_root_dir_change(MockEvent())

    assert "C:\\Users\\test\\output" in view.output_zip_field.hint_text
    assert "可使用翻譯.zip" in view.output_zip_field.hint_text


def test_start_bundling_without_required_paths_shows_error():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    view.start_bundling_clicked(None)

    assert page.overlay
    assert "請填寫" in page.overlay[-1].content.value


def test_bundling_without_output_zip_shows_no_error(monkeypatch):
    """output_zip 留空時不應顯示錯誤，會自動帶入"""
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    view._config_output_zip_name = "可使用翻譯.zip"
    monkeypatch.setattr(view.log_view._list_view, "scroll_to", lambda **kwargs: None)

    def mock_generator(**kwargs):
        return iter([{"log": "done", "progress": 1.0}])

    monkeypatch.setattr(
        "app.views.bundler_view.bundle_outputs_generator",
        mock_generator,
    )

    view.root_dir_field.value = "C:/test"
    view.output_zip_field.value = ""

    view.start_bundling_clicked(None)

    assert len(page.overlay) == 0


def test_bundling_worker_updates_progress_and_reenables_controls(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    monkeypatch.setattr(view.log_view._list_view, "scroll_to", lambda **kwargs: None)

    def mock_generator(**kwargs):
        return iter(
            [
                {"log": "step1", "progress": 0.5},
                {"log": "done", "progress": 1.0},
            ]
        )

    monkeypatch.setattr(
        "app.views.bundler_view.bundle_outputs_generator",
        mock_generator,
    )

    view._bundling_worker("C:/Root", "C:/out.zip", 34, None, "", "")
    _drain_ui_tasks(page)

    assert view.progress_bar.value == 1.0


def test_bundling_worker_with_version_info(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    monkeypatch.setattr(view.log_view._list_view, "scroll_to", lambda **kwargs: None)

    captured_kwargs = {}

    def mock_generator(**kwargs):
        captured_kwargs.update(kwargs)
        return iter(
            [
                {"log": "done", "progress": 1.0},
            ]
        )

    monkeypatch.setattr(
        "app.views.bundler_view.bundle_outputs_generator",
        mock_generator,
    )

    view._bundling_worker("C:/Root", "C:/out.zip", 15, 999, "Test Description", "")

    assert captured_kwargs["pack_format"] == 15
    assert captured_kwargs["max_pack_format"] == 999
    assert captured_kwargs["description"] == "Test Description"


def test_bundling_worker_passes_extra_folders(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    view.extra_folders = ["C:/extra1", "C:/extra2"]
    monkeypatch.setattr(view.log_view._list_view, "scroll_to", lambda **kwargs: None)

    captured_kwargs = {}

    def mock_generator(**kwargs):
        captured_kwargs.update(kwargs)
        return iter([{"log": "done", "progress": 1.0}])

    monkeypatch.setattr(
        "app.views.bundler_view.bundle_outputs_generator",
        mock_generator,
    )

    view._bundling_worker("C:/Root", "C:/out.zip", 34, None, "", "")

    assert captured_kwargs["extra_folders"] == ["C:/extra1", "C:/extra2"]


def test_bundling_worker_passes_pack_image(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    monkeypatch.setattr(view.log_view._list_view, "scroll_to", lambda **kwargs: None)

    captured_kwargs = {}

    def mock_generator(**kwargs):
        captured_kwargs.update(kwargs)
        return iter([{"log": "done", "progress": 1.0}])

    monkeypatch.setattr(
        "app.views.bundler_view.bundle_outputs_generator",
        mock_generator,
    )

    view._bundling_worker("C:/Root", "C:/out.zip", 34, None, "", "C:/pack.png")

    assert captured_kwargs["pack_image_path"] == "C:/pack.png"


def test_bundling_worker_empty_pack_image(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    view.pack_image_field.value = ""
    monkeypatch.setattr(view.log_view._list_view, "scroll_to", lambda **kwargs: None)

    captured_kwargs = {}

    def mock_generator(**kwargs):
        captured_kwargs.update(kwargs)
        return iter([{"log": "done", "progress": 1.0}])

    monkeypatch.setattr(
        "app.views.bundler_view.bundle_outputs_generator",
        mock_generator,
    )

    view._bundling_worker("C:/Root", "C:/out.zip", 34, None, "", "")

    assert captured_kwargs["pack_image_path"] is None


def test_remove_extra_folder(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    view.extra_folders = ["C:/folder1", "C:/folder2"]
    monkeypatch.setattr(view, "_refresh_extra_folders", lambda: None)
    monkeypatch.setattr(view, "update", lambda: None)
    monkeypatch.setattr(view.log_view._list_view, "scroll_to", lambda **kwargs: None)

    view._remove_extra_folder("C:/folder1")

    assert "C:/folder1" not in view.extra_folders
    assert "C:/folder2" in view.extra_folders


def test_extra_folders_list_initially_empty():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    assert view.extra_folders == []
    assert len(view.extra_folders_view.controls) == 0


def test_show_snack_bar_adds_to_overlay(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    show_snack(view.page, "Test message")

    assert len(page.overlay) == 1
    assert page.overlay[-1].content.value == "Test message"


def test_bundling_worker_with_error(monkeypatch):
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    monkeypatch.setattr(view.log_view._list_view, "scroll_to", lambda **kwargs: None)

    def mock_generator(**kwargs):
        raise RuntimeError("Test error")

    monkeypatch.setattr(
        "app.views.bundler_view.bundle_outputs_generator",
        mock_generator,
    )

    view._bundling_worker("C:/Root", "C:/out.zip", 34, None, "", "")
    _drain_ui_tasks(page)

    assert view.progress_bar.color == C.RED
    assert not view.progress_bar.visible


def test_bundler_view_show_snack_bar_adds_to_overlay():
    """測試 _show_snack_bar 正確將 SnackBar 加入 page.overlay"""
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    show_snack(view.page, "Test error", "#FF0000")

    assert len(page.overlay) == 1
    assert page.overlay[0].open is True


def test_bundler_view_append_log_adds_control():
    """測試 _append_log 正確將日誌行加入 log_view"""
    page = mock_page()
    view = BundlerView(page, mock_filepicker())

    view._append_log("test log entry")

    # PR refactor/unified-log-view: log_view 內部 ListView 透過 _list_view 存取
    assert len(view.log_view._list_view.controls) >= 1


def test_bundler_view_pick_pack_image():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    assert hasattr(view, "_pick_pack_image")


def test_bundler_view_extra_folders_list():
    page = mock_page()
    view = BundlerView(page, mock_filepicker())
    assert view.extra_folders is not None
    assert isinstance(view.extra_folders, list)


def _source_with_mcmeta(tmp_path, content: str):
    src = tmp_path / "src"
    src.mkdir()
    (src / "pack.mcmeta").write_text(content, encoding="utf-8")
    return src


def test_preview_shows_source_pack_mcmeta_that_overrides_the_ui(tmp_path):
    src = _source_with_mcmeta(
        tmp_path, '{"pack":{"description":"來源","pack_format":15}}'
    )
    view = BundlerView(mock_page(), mock_filepicker())
    view.version_field.value = "1.21.1"
    view.root_dir_field.value = str(src)
    view._update_preview()

    assert json.loads(view.mcmeta_view.value)["pack"]["pack_format"] == 15
    assert view.preview_format.value == "pack_format 15"
    assert view.mcmeta_source_note.visible
    assert "覆蓋" in view.mcmeta_source_note.value
    assert str(src / "pack.mcmeta") in view.mcmeta_source_note.value


def test_preview_shows_the_repaired_source_and_what_was_fixed(tmp_path):
    src = _source_with_mcmeta(
        tmp_path, '{"pack":{"description":"d","pack_format":"34"}}'
    )
    view = BundlerView(mock_page(), mock_filepicker())
    view.root_dir_field.value = str(src)
    view._update_preview()

    assert json.loads(view.mcmeta_view.value)["pack"]["pack_format"] == 34
    assert "修正" in view.mcmeta_source_note.value


def test_preview_falls_back_to_ui_when_source_is_unusable(tmp_path):
    src = _source_with_mcmeta(tmp_path, "{not json")
    view = BundlerView(mock_page(), mock_filepicker())
    view.version_field.value = "1.21.1"
    view.root_dir_field.value = str(src)
    view._update_preview()

    assert json.loads(view.mcmeta_view.value)["pack"]["pack_format"] == 34
    assert "無法使用" in view.mcmeta_source_note.value


def test_preview_has_no_note_without_a_source_file(tmp_path):
    view = BundlerView(mock_page(), mock_filepicker())
    view.root_dir_field.value = str(tmp_path)
    view._update_preview()
    assert not view.mcmeta_source_note.visible and view.mcmeta_source_note.value == ""


def test_preview_follows_root_dir_and_extra_folder_changes(tmp_path):
    src = _source_with_mcmeta(tmp_path, '{"pack":{"description":"d","pack_format":15}}')
    view = BundlerView(mock_page(), mock_filepicker())
    view.root_dir_field.value = str(tmp_path / "other")
    view._on_root_dir_change(None)
    assert not view.mcmeta_source_note.visible

    view.root_dir_field.value = str(src)
    view._on_root_dir_change(None)
    assert view.mcmeta_source_note.visible

    view.root_dir_field.value = str(tmp_path)
    extra = tmp_path / "extra.mcmeta" / "pack.mcmeta"
    extra.parent.mkdir()
    extra.write_text('{"pack":{"description":"x","pack_format":9}}', encoding="utf-8")
    view.extra_folders.append(str(extra))
    view._refresh_extra_folders()
    view._update_preview()
    assert json.loads(view.mcmeta_view.value)["pack"]["pack_format"] == 9


def test_preview_card_uses_source_description_and_png(tmp_path):
    src = _source_with_mcmeta(
        tmp_path, '{"pack":{"description":"來源描述","pack_format":15}}'
    )
    png = src / "pack.png"
    png.write_bytes(b"\x89PNG")
    ui_png = tmp_path / "ui.png"
    ui_png.write_bytes(b"\x89PNG")
    view = BundlerView(mock_page(), mock_filepicker())
    view.description_field.value = "UI 描述"
    view.pack_image_field.value = str(ui_png)
    view.root_dir_field.value = str(src)
    view._update_preview()

    assert "".join(s.text for s in view.preview_title.spans) == "來源描述"
    assert view.preview_image.content.src == str(png)
    assert view.preview_source_note.visible
    assert str(png) in view.preview_source_note.value


def test_preview_card_uses_ui_values_without_source_files(tmp_path):
    ui_png = tmp_path / "ui.png"
    ui_png.write_bytes(b"\x89PNG")
    view = BundlerView(mock_page(), mock_filepicker())
    view.description_field.value = "UI 描述"
    view.pack_image_field.value = str(ui_png)
    view.root_dir_field.value = str(tmp_path)
    view._update_preview()

    assert "".join(s.text for s in view.preview_title.spans) == "UI 描述"
    assert view.preview_image.content.src == str(ui_png)
    assert not view.preview_source_note.visible


def test_picking_a_pack_image_refreshes_the_preview(tmp_path):
    import asyncio
    from types import SimpleNamespace

    png = tmp_path / "chosen.png"
    png.write_bytes(b"\x89PNG")
    view = BundlerView(mock_page(), mock_filepicker())

    async def pick_files(**_kwargs):
        return [SimpleNamespace(path=str(png))]

    view.file_picker.pick_files = pick_files
    asyncio.run(view._async_pick_pack_image())

    assert view.preview_image.content.src == str(png)
