"""圖示預覽的列控制項重用：換頁只更新值，不重建控制項。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.views.icon_preview.mod_row import ModRow
from app.views.icon_preview_row import LangItemRow
from app.views.icon_preview_view import IconPreviewView
from tests.conftest import mock_page


def _drain(page):
    while page._tasks:
        handler, args = page._tasks.pop(0)
        asyncio.run(handler(*args))


def _entries(prefix: str, n: int):
    return [
        SimpleNamespace(key=f"item.demo.{prefix}{i}", en=f"En {prefix}{i}", zh_tw="")
        for i in range(n)
    ]


def _detail_view(entries, page_size=50):
    page = mock_page()
    view = IconPreviewView(page)
    view.source_root = Path(".")
    view.current_modid = "demo"
    view.mods = {"demo": entries}
    view._zh_data = {e.key: f"譯 {e.key}" for e in entries[:3]}
    view.page_size = page_size
    return page, view


def test_changing_detail_page_rebinds_the_same_row_controls():
    page, view = _detail_view(_entries("a", 120), page_size=50)
    view._render_current_page()
    _drain(page)
    first_rows = list(view.list_view.controls)
    assert len(first_rows) == 50
    assert first_rows[0].key_field.value == "item.demo.a0"
    assert first_rows[0].zh_field.value == "譯 item.demo.a0"

    view.current_page = 2  # 最後一頁只有 20 筆
    view._render_current_page()
    _drain(page)

    rows = view.list_view.controls
    assert len(rows) == 20
    assert all(a is b for a, b in zip(rows, first_rows, strict=False))
    assert rows[0].key_field.value == "item.demo.a100"
    assert rows[0].en_field.value == "En a100"
    assert rows[0].zh_field.value == ""
    assert rows[0].lang_key == "item.demo.a100"

    view.current_page = 0
    view._render_current_page()
    _drain(page)
    assert len(view.list_view.controls) == 50
    assert view.list_view.controls[10] is first_rows[10]


def test_edit_callback_uses_the_currently_bound_key():
    page, view = _detail_view(_entries("a", 60), page_size=50)
    view._render_current_page()
    _drain(page)
    view.current_page = 1
    view._render_current_page()
    _drain(page)
    row = view.list_view.controls[0]

    row.zh_field.on_change(SimpleNamespace(control=SimpleNamespace(value="新譯文")))

    assert view._zh_data["item.demo.a50"] == "新譯文"
    assert (
        "item.demo.a0" not in view._zh_data or view._zh_data["item.demo.a0"] != "新譯文"
    )


def test_rapid_page_changes_apply_only_the_latest_page():
    page, view = _detail_view(_entries("a", 200), page_size=50)
    for target in (1, 2, 3):
        view.current_page = target
        view._render_current_page()
    _drain(page)  # 先排入的較舊結果不得覆蓋最後一頁

    rows = view.list_view.controls
    assert rows[0].key_field.value == "item.demo.a150"
    assert view.page_info.value.endswith("第 4 / 4 頁")


def test_page_size_change_in_detail_changes_item_page_size_only():
    page, view = _detail_view(_entries("a", 120), page_size=50)
    view.mod_page_size = 50
    view._render_current_page()
    _drain(page)

    view._on_page_size_change(SimpleNamespace(control=SimpleNamespace(value="25")))
    _drain(page)

    assert view.page_size == 25
    assert view.mod_page_size == 50
    assert len(view.list_view.controls) == 25
    assert all(isinstance(row, LangItemRow) for row in view.list_view.controls)


def test_lang_row_bind_toggles_icon_and_reason_without_new_controls(tmp_path):
    from app.services_impl.icon_service import IconResult, IconRisk

    row = LangItemRow(
        lang_key="",
        en_text="",
        zh_text="",
        assets_root=tmp_path,
        preview_root=tmp_path,
        on_value_changed=lambda k, v: None,
        prepared_icon=(IconResult(icon_path=None, reason="", risk=None), None),
    )
    image, risk = row.icon_image, row.risk_label
    controls = list(row.content.controls)

    content_result = IconResult(icon_path=None, reason="找不到貼圖", risk=IconRisk.WARN)
    row.bind("item.m.ruby", "Ruby", "紅寶石", (content_result, None))
    assert image.visible and image.tooltip  # 內容類 key：共用佔位圖
    assert risk.visible and "找不到貼圖" in risk.value

    row.bind("advancements.m.title", "Title", "", (content_result, None))
    assert not image.visible  # 非內容類 key 不佔版面
    assert risk.visible

    row.bind("item.m.gem", "Gem", "", (IconResult("x", "", None), b"png-bytes"))
    assert image.visible and image.src == b"png-bytes"
    assert not risk.visible and risk.value == ""
    assert row.icon_image is image and row.risk_label is risk
    assert row.content.controls == controls


def test_mod_list_pages_reuse_row_controls_and_rebind_counts():
    page = mock_page()
    view = IconPreviewView(page)
    view.mods = {
        f"mod{i:03d}": [
            SimpleNamespace(key="k", en="e", zh_tw="" if i % 2 else "譯")
            for _ in range(i + 1)
        ]
        for i in range(120)
    }
    view.mod_page_size = 50
    view._render_mod_list()
    first = list(view.list_view.controls)
    assert len(first) == 50 and all(isinstance(r, ModRow) for r in first)
    assert first[1]._counts.value == "總數 2 ｜ 未翻譯 2"
    assert first[0]._chip_text.value == "完成"

    view.mod_current_page = 2
    view._render_mod_list()

    rows = view.list_view.controls
    assert len(rows) == 20
    assert all(a is b for a, b in zip(rows, first, strict=False))
    assert rows[0].modid == "mod100"
    assert rows[1]._counts.value == "總數 102 ｜ 未翻譯 102"
    opened = []
    rows[0]._on_open = opened.append
    rows[0].on_click(None)
    assert opened == ["mod100"]


def test_mod_list_prefetches_neighbor_pages_off_the_event_loop():
    """換頁前先在背景載入前後兩頁，換頁時不必在 event loop 上讀分片。"""
    import threading

    from app.views.icon_preview.mod_store import MemorySource, ModEntryStore

    loaded = []

    class Source(MemorySource):
        def load(self, modid):
            loaded.append((modid, threading.get_ident()))
            return super().load(modid)

    by_mod = {
        f"mod{i:03d}": [{"modid": f"mod{i:03d}", "key": "k", "en": "e"}]
        for i in range(150)
    }
    page = mock_page()
    view = IconPreviewView(page)
    view.mods = ModEntryStore(Source(by_mod), {})
    view.mod_page_size = 50
    view.mod_current_page = 1
    view._render_mod_list()
    _drain(page)  # 預載工作（to_thread）

    main = threading.get_ident()
    in_loop = {m for m, t in loaded if t == main}
    prefetched = {m for m, t in loaded if t != main}
    assert in_loop == {f"mod{i:03d}" for i in range(50, 100)}
    assert prefetched == {
        f"mod{i:03d}" for i in list(range(50)) + list(range(100, 150))
    }

    loaded.clear()
    view.mod_current_page = 2
    view._render_mod_list()
    assert loaded == []  # 已預載，換頁不再讀


def test_detail_render_prefetches_next_page_icons(monkeypatch):
    import threading

    entries = [
        SimpleNamespace(
            key=f"item.demo.a{i}", en="e", zh_tw="", icon_path=f"jar://x#{i}"
        )
        for i in range(120)
    ]
    page, view = _detail_view(entries, page_size=50)
    prepared_for = []

    def fake_prepare(batch, context):
        prepared_for.append((len(batch), threading.get_ident()))
        return [(SimpleNamespace(reason="", risk=None, icon_path=None), None)] * len(
            batch
        )

    monkeypatch.setattr(view, "_prepare_row_icons", fake_prepare)
    view._render_current_page()
    _drain(page)

    assert sorted(n for n, _ in prepared_for) == [50, 50]  # 本頁與下一頁
    assert all(t != threading.get_ident() for _, t in prepared_for)
