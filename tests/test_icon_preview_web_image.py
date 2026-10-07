from types import SimpleNamespace

import flet as ft

from app.views import icon_preview_row as rows

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_row_image_uses_bytes_source_for_web(monkeypatch, tmp_path):
    preview_path = tmp_path / "preview.png"
    preview_path.write_bytes(PNG_BYTES)
    icon_result = SimpleNamespace(reason="", risk=None, icon_path=None)

    monkeypatch.setattr(rows, "resolve_icon_with_reason", lambda *_: icon_result)
    monkeypatch.setattr(rows, "_resolve_preview_path", lambda *_: preview_path)
    monkeypatch.setattr(rows, "_ensure_icon_size", lambda path: path)

    prepared_result, image_bytes = rows.prepare_row_icon(
        "item.demo.test", tmp_path, tmp_path, None
    )
    assert prepared_result is icon_result
    assert image_bytes == PNG_BYTES

    row = rows.LangItemRow(
        lang_key="item.demo.test",
        en_text="Test item",
        zh_text="測試",
        assets_root=tmp_path,
        preview_root=tmp_path,
        on_value_changed=lambda _key, _value: None,
        prepared_icon=(prepared_result, image_bytes),
    )

    image = row.content.controls[0]
    assert isinstance(image, ft.Image)
    assert image.src == PNG_BYTES
