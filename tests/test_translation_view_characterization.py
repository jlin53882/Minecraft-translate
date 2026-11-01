import flet as ft

from app.ui.design import tone as get_tone
from app.ui.snack import show_snack
from app.views import translation_view as tv
from tests.conftest import mock_filepicker, mock_page


class _Session:
    def __init__(self):
        self.started = 0

    def start(self):
        self.started += 1


def test_translation_view_is_markdown_only_with_shared_status_panel(monkeypatch):
    monkeypatch.setattr(tv, "TaskSession", _Session)
    view = tv.TranslationView(mock_page(), mock_filepicker())

    # FTB／KubeJS 已移到流水線頁；本頁只保留 Markdown 的輸入與步驟。
    assert hasattr(view, "md_in_dir")
    assert not hasattr(view, "ftb_in_dir")
    assert not hasattr(view, "kjs_in_dir")
    assert view.status_chip.label.value == "尚未開始"
    assert view.progress.value == 0
    assert view.log_panel.visible is False
    assert view.cancel_button.visible is False


def test_translation_log_panel_opens_only_after_run_or_user_message(monkeypatch):
    monkeypatch.setattr(tv, "TaskSession", _Session)
    view = tv.TranslationView(mock_page(), mock_filepicker())

    assert view.log_panel.visible is False
    view._append_log("[UI] 測試訊息")
    assert view.log_panel.visible is True


def test_reset_md_inputs_restores_defaults_and_appends_log(monkeypatch):
    monkeypatch.setattr(tv, "TaskSession", _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)

    view.md_in_dir.value = "X"
    view.md_out_dir.value = "Y"
    view.md_step_extract.value = False
    view.md_step_translate.value = False
    view.md_step_inject.value = False
    view.md_write_new_cache.value = True
    view.md_lang_mode.value = "all"

    view._reset_md_inputs()

    assert view.md_in_dir.value == ""
    assert view.md_out_dir.value == ""
    assert view.md_step_extract.value is True
    assert view.md_step_translate.value is True
    assert view.md_step_inject.value is True
    assert view.md_write_new_cache.value is False
    assert view.md_lang_mode.value == "non_cjk_only"
    assert (
        view.log_view._list_view.controls[-1].value
        == "[UI] 已重置：Markdown 輸入已清空"
    )  # PR refactor/unified-log-view


def test_clear_logs_removes_all_controls(monkeypatch):
    """驗證 _clear_logs 正確清除所有日誌"""
    monkeypatch.setattr(tv, "TaskSession", _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)

    view._append_log("line1")
    view._append_log("line2")
    # PR refactor/unified-log-view: log_view 內部 ListView 透過 _list_view 存取
    # LogView 預設沒初始 placeholder（不像舊裸 ListView 加 "等待翻譯開始..."）
    assert len(view.log_view._list_view.controls) == 2

    view._clear_logs()

    assert len(view.log_view._list_view.controls) == 0


def test_append_log_trims_to_max_400_lines(monkeypatch):
    """驗證 _append_log 不會无限增长"""
    monkeypatch.setattr(tv, "TaskSession", _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)

    for i in range(450):
        view._append_log(f"log{i}")

    # PR refactor/unified-log-view: LogView 內部 max_lines 截斷（250 default tail_lines）
    assert len(view.log_view._list_view.controls) <= 250


def test_set_status_updates_chip_label_and_color(monkeypatch):
    """驗證 _set_status 更新 status_chip 的文字與背景顏色"""
    monkeypatch.setattr(tv, "TaskSession", _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)

    view._set_status("工作中", "gold")

    assert view.status_chip.label.value == "工作中"
    assert view.status_chip.bgcolor == get_tone("gold").bg
    # 相容 actions 層仍傳舊背景色：依色系轉成對應色組
    view._set_status("完成", ft.Colors.GREEN_200)
    assert view.status_chip.bgcolor == get_tone("em").bg


def test_pick_directory_into_sets_target_field(monkeypatch):
    """驗證 _pick_directory_into 正確設定目標欄位"""
    monkeypatch.setattr(tv, "TaskSession", _Session)
    page = mock_page()
    picker = mock_filepicker()
    picker.set_mock_path("/test/path")
    view = tv.TranslationView(page, picker)

    target = tv.ft.TextField()
    view._pick_directory_into(target)
    page._run_all_tasks()

    assert target.value == "/test/path"
    assert page.updated >= 1


def test_show_snack_adds_to_page_overlay(monkeypatch):
    """驗證 _show_snack 正確將 SnackBar 加入 page.overlay"""
    monkeypatch.setattr(tv, "TaskSession", _Session)
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)

    show_snack(view.page, "Test message", "#00FF00")

    assert len(page.overlay) == 1
    assert page.overlay[0].open is True


def test_run_md_calls_run_md_service(monkeypatch):
    """驗證 _run_md 正確呼叫 run_md"""
    page = mock_page()
    picker = mock_filepicker()
    calls = {}

    monkeypatch.setattr(tv, "TaskSession", _Session)
    monkeypatch.setattr(
        tv.threading,
        "Thread",
        lambda target=None, daemon=None: type(
            "T", (), {"start": lambda self: target()}
        )(),
    )
    monkeypatch.setattr(tv.TranslationView, "_start_ui_timer", lambda self: None)

    def fake_service(input_dir, session, **kwargs):
        calls["input_dir"] = input_dir
        calls.update(kwargs)

    monkeypatch.setattr(tv, "run_md_translation_service", fake_service)

    view = tv.TranslationView(page, picker)
    view.md_in_dir.value = "C:/MD/In"
    view.md_out_dir.value = "C:/MD/Out"
    view.md_step_extract.value = True
    view.md_step_translate.value = True
    view.md_step_inject.value = False
    view.md_write_new_cache.value = True

    view._run_md(dry_run=True)

    assert calls["input_dir"] == "C:/MD/In"
    assert calls["output_dir"] == "C:/MD/Out"
    assert calls["dry_run"] is True
    assert calls["step_inject"] is False


def test_pick_directory_into_without_page_update_when_no_result(monkeypatch):
    """驗證 _pick_directory_into 在取消選擇時不更新"""
    page = mock_page()
    picker = mock_filepicker()
    picker._mock_path = None
    view = tv.TranslationView(page, picker)

    target = tv.ft.TextField(value="original")
    view._pick_directory_into(target)
    page._run_all_tasks()

    assert target.value == "original"
    assert page.updated == 0


def test_translation_view_show_snack_adds_to_overlay():
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)
    show_snack(view.page, "Test error", "#FF0000")
    assert len(page.overlay) >= 1


def test_translation_view_path_row_exists():
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)
    assert view._path_row is not None


def test_translation_view_action_row_exists():
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)
    assert view._action_row is not None


def test_translation_view_async_pick_directory_into_exists():
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)
    assert hasattr(view, "_async_pick_directory_into")
    assert callable(view._async_pick_directory_into)


def test_translation_view_status_chip_exists():
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)
    assert view.status_chip is not None


def test_translation_view_progress_exists():
    page = mock_page()
    picker = mock_filepicker()
    view = tv.TranslationView(page, picker)
    assert view.progress is not None


# -- #164：重開後續跑 ---------------------------------------------------------------


def _resume_view(monkeypatch):
    """建立任務翻譯頁，並把三個服務換成記錄呼叫的替身（執行緒改為同步）。"""
    calls: list[tuple[str, tuple, dict]] = []
    monkeypatch.setattr(tv, "TaskSession", _Session)
    monkeypatch.setattr(
        tv.threading,
        "Thread",
        lambda target=None, daemon=None: type(
            "T", (), {"start": lambda self: target()}
        )(),
    )
    monkeypatch.setattr(tv.TranslationView, "_start_ui_timer", lambda self: None)
    monkeypatch.setattr(
        tv,
        "run_md_translation_service",
        lambda *a, **k: calls.append(("md", a, k)),
    )
    return tv.TranslationView(mock_page(), mock_filepicker()), calls


def _task(kind, **options):
    return type(
        "Task",
        (),
        {
            "kind": kind,
            "input_dir": "C:/Pack",
            "output_dir": "C:/Out",
            "options": options,
        },
    )()


def test_resume_md_restores_the_language_mode(monkeypatch):
    view, calls = _resume_view(monkeypatch)

    view.resume_interrupted(_task("md", lang_mode="all", write_new_cache=False))

    ((name, _args, kwargs),) = calls
    assert name == "md"
    assert kwargs["input_dir"] == "C:/Pack" and kwargs["output_dir"] == "C:/Out"
    assert kwargs["lang_mode"] == "all" and kwargs["write_new_cache"] is False
    assert kwargs["dry_run"] is False and kwargs["step_translate"] is True


def test_resume_with_missing_options_falls_back_to_the_defaults(monkeypatch):
    view, calls = _resume_view(monkeypatch)

    view.resume_interrupted(_task("md"))

    ((_n, _a, kwargs),) = calls
    assert kwargs["lang_mode"] == "non_cjk_only"
    assert kwargs["step_extract"] is True and kwargs["step_inject"] is True


def test_resume_does_not_start_a_second_run(monkeypatch):
    view, calls = _resume_view(monkeypatch)
    view._ui_timer_running = True  # 已有任務在跑

    view.resume_interrupted(_task("md"))

    assert calls == []


def test_resume_of_an_unknown_kind_starts_nothing(monkeypatch):
    view, calls = _resume_view(monkeypatch)

    view.resume_interrupted(_task("something_else"))

    assert calls == []
