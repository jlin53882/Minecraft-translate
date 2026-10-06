"""單行輸入框預設掛 on_change，讓 Web 手動輸入的值即時同步回後端。"""

from app.ui import kit


def test_single_line_field_syncs_on_change_by_default():
    assert kit.text_field("路徑").on_change is not None


def test_explicit_handler_is_kept():
    def handler(e):
        pass

    assert kit.text_field("x", on_change=handler).on_change is handler


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
