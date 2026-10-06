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
