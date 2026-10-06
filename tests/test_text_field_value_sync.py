"""單行輸入框預設掛 on_change，讓 Web 手動輸入的值即時同步回後端。"""

from app.ui import kit


def test_single_line_field_syncs_on_change_by_default():
    assert kit.text_field("路徑").on_change is not None


def test_caller_handler_runs_after_the_web_value_is_synced():
    """Web：事件資料是新值、控制項仍是舊值時，呼叫端 handler 讀到的必須是新值。"""
    from types import SimpleNamespace

    from app.ui.sync_text_field import SyncTextField

    seen = []
    field = SyncTextField(label="x", on_change=lambda e: seen.append(e.control.value))
    field.value = "舊路徑"

    field.on_change(SimpleNamespace(control=field, data="新路徑", name="change"))

    assert seen == ["新路徑"]


def test_caller_handler_supports_async_and_zero_arg_styles():
    import asyncio
    from types import SimpleNamespace

    from app.ui.sync_text_field import SyncTextField

    calls = []

    async def async_handler(e):
        calls.append(("async", e.control.value))

    def zero_arg():
        calls.append(("zero", None))

    field = SyncTextField(label="x", on_change=async_handler)
    field.value = "舊"
    asyncio.run(
        field.on_change(SimpleNamespace(control=field, data="新", name="change"))
    )
    other = SyncTextField(label="y", on_change=zero_arg)
    other.on_change(SimpleNamespace(control=other, data="新", name="change"))

    assert calls == [("async", "新"), ("zero", None)]


def test_caller_blur_handler_also_syncs_first():
    from types import SimpleNamespace

    from app.ui.sync_text_field import SyncTextField

    seen = []
    field = SyncTextField(label="x", on_blur=lambda e: seen.append(e.control.value))
    field.value = ""

    field.on_blur(SimpleNamespace(control=field, data="新路徑", name="blur"))

    assert seen == ["新路徑"]


def test_handlers_are_not_wrapped_twice():
    from app.ui.sync_text_field import SyncTextField

    field = SyncTextField(label="x", on_change=lambda e: None)
    first = field.on_change
    field._ensure_sync_handlers()
    assert field.on_change is first


def test_sync_handler_writes_event_value_back_to_control():
    from types import SimpleNamespace

    from app.ui.sync_text_field import _sync_value

    control = SimpleNamespace(value="")
    _sync_value(SimpleNamespace(control=control, data="使用者剛輸入的值"))

    assert control.value == "使用者剛輸入的值"


def test_sync_handler_does_not_clear_existing_value_on_empty_blur_event():
    from types import SimpleNamespace

    from app.ui.sync_text_field import _sync_value

    control = SimpleNamespace(value="完整路徑")
    _sync_value(SimpleNamespace(control=control, data=""))

    assert control.value == "完整路徑"


def test_change_event_value_overwrites_stale_control_value():
    """Web change 事件帶回新值時，不得保留 Python 端的舊值。"""
    from types import SimpleNamespace

    from app.ui.sync_text_field import _sync_value

    control = SimpleNamespace(value="舊路徑")
    _sync_value(SimpleNamespace(control=control, data="新路徑", name="change"))

    assert control.value == "新路徑"


def test_web_change_without_event_name_updates_backend_cache_without_dirty_patch():
    """實際 Web 事件沒有可靠的 name 時，也要同步且不能反推舊值到瀏覽器。"""
    from types import SimpleNamespace

    from app.ui.sync_text_field import _sync_change

    control = SimpleNamespace(value="舊路徑", _values={"value": "舊路徑"}, _dirty={})
    _sync_change(SimpleNamespace(control=control, data="C:\\Users\\完整長路徑"))

    assert control._values["value"] == "C:\\Users\\完整長路徑"
    assert control._dirty == {}


def test_web_change_can_sync_deletion_to_empty_value():
    from types import SimpleNamespace

    from app.ui.sync_text_field import _sync_change

    control = SimpleNamespace(value="舊值", _values={"value": "舊值"}, _dirty={})
    _sync_change(SimpleNamespace(control=control, data=""))

    assert control._values == {}
    assert control._dirty == {}


def test_single_line_field_has_blur_fallback():
    assert kit.text_field("路徑").on_blur is not None
    assert kit.text_field("x", multiline=True).on_blur is None


def test_multiline_and_password_fields_do_not_round_trip_every_key():
    assert kit.text_field("x", multiline=True).on_change is None
    assert kit.text_field("x", password=True).on_change is None


def test_every_single_line_field_in_app_uses_sync_text_field():
    """app/ 內不得直接建立 ft.TextField：統一用 SyncTextField，Web 輸入值才會同步。"""
    import ast
    from pathlib import Path

    app_dir = Path(__file__).resolve().parent.parent / "app"
    files = list(app_dir.rglob("*.py"))
    assert len(files) > 50  # 找不到檔案時不能假通過
    offenders = []
    for path in files:
        if path.name == "sync_text_field.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "TextField"
            ):
                offenders.append(f"{path.relative_to(app_dir)}:{node.lineno}")
    assert not offenders, offenders


def test_sync_text_field_covers_dialog_style_fields():
    from app.ui.sync_text_field import SyncTextField

    assert SyncTextField(label="輸出 ZIP 檔案").on_change is not None
    assert SyncTextField(multiline=True).on_change is None
    assert SyncTextField(password=True).on_change is None
    assert SyncTextField(read_only=True).on_change is None


def test_blur_handler_logs_what_the_backend_sees(caplog):
    """失焦時記錄控制項值與事件資料，Web 同步問題可直接從 log 判斷。"""
    import logging
    from types import SimpleNamespace

    from app.ui.sync_text_field import _sync_blur

    control = SimpleNamespace(value="舊路徑", label="輸入", hint_text="請選擇")
    with caplog.at_level(logging.INFO):
        _sync_blur(SimpleNamespace(control=control, data="新路徑"))

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "[欄位同步] blur" in text and "舊路徑" in text and "新路徑" in text
    assert control.value == "舊路徑"  # 控制項已有值時不被事件資料覆蓋（既有規則不變）


def test_single_line_field_blur_uses_the_logging_handler():
    from app.ui.sync_text_field import SyncTextField, _sync_blur

    assert SyncTextField(label="x").on_blur is _sync_blur
