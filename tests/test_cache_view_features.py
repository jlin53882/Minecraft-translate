import json

import flet as ft

from app.views import cache_view as cache_view_module
from app.views.cache_view import CacheView


class FakePage:
    def __init__(self):
        self.updated = 0
        self.overlay = []
        self.clipboard = ""

    def update(self):
        self.updated += 1

    def set_clipboard(self, text: str):
        self.clipboard = text


def test_load_shard_rows_includes_cleaned_shards_and_excludes_cache_main(tmp_path):
    cache_root = tmp_path / "cache_root"
    lang_dir = cache_root / "lang"
    lang_dir.mkdir(parents=True)

    # Should be excluded
    (lang_dir / "lang_cache_main.json").write_text("{}", encoding="utf-8")
    # Should be included
    (lang_dir / "lang_00001.json").write_text(json.dumps({"k1": {}}, ensure_ascii=False), encoding="utf-8")
    (lang_dir / "cleaned_shard_00002.json").write_text(json.dumps({"k2": {}, "k3": {}}, ensure_ascii=False), encoding="utf-8")

    view = CacheView.__new__(CacheView)
    view._last_overview_data = {"cache_root": str(cache_root)}

    rows = CacheView._load_shard_rows(view, "lang", "00001", 2500)
    names = [r["filename"] for r in rows]

    assert "lang_cache_main.json" not in names
    assert "lang_00001.json" in names
    assert "cleaned_shard_00002.json" in names
    active = [r for r in rows if r["is_active"]]
    assert len(active) == 1
    assert active[0]["filename"] == "lang_00001.json"


def test_format_shard_src_text_preview_and_raw_modes():
    view = CacheView.__new__(CacheView)

    src = "line1\\nline2"
    preview = CacheView._format_shard_src_text(view, src, "preview")
    raw = CacheView._format_shard_src_text(view, src, "raw")

    assert preview == "line1\nline2"
    assert raw.startswith('"') and raw.endswith('"')
    assert "\\\\n" in raw


def test_on_shard_dst_apply_updates_cache_and_history(monkeypatch):
    view = CacheView.__new__(CacheView)
    view.page = FakePage()
    view.ui_busy = False

    view.shard_detail_selected_type = "lang"
    view.shard_detail_selected_file = "lang_00001.json"
    view.shard_detail_selected_key = "k1"
    view.shard_dst_original = "old"
    view.shard_dst_loaded_sig = ("lang", "lang_00001.json", "k1")
    view.shard_dst_field = ft.TextField(value="new")

    view.query_results = [{"cache_type": "lang", "key": "k1", "preview": "old"}]

    notify_calls = []
    history_calls = []
    save_calls = []
    update_calls = []

    view._notify = lambda msg, level="info": notify_calls.append((level, msg))
    view._history_now_ts = lambda: "2026-02-12T02:00:00+08:00"
    view._history_append_event = lambda ctype, event: history_calls.append((ctype, event))
    view._render_query_results = lambda: None
    view._render_query_detail = lambda: None
    view._render_shard_src_panel = lambda: None
    view._render_shard_dst_panel = lambda: None
    view._refresh_disabled_state = lambda: None

    monkeypatch.setattr(cache_view_module, "cache_update_dst_service", lambda ctype, key, new_dst: update_calls.append((ctype, key, new_dst)) or True)
    monkeypatch.setattr(cache_view_module, "cache_save_all_service", lambda **kwargs: save_calls.append(kwargs) or {"ok": True})

    CacheView._on_shard_dst_apply(view, None)

    assert update_calls == [("lang", "k1", "new")]
    assert save_calls == [{"write_new_shard": False, "only_types": ["lang"]}]
    assert history_calls and history_calls[0][0] == "lang"
    assert history_calls[0][1]["action"] == "apply_from_shard_detail"
    assert history_calls[0][1]["old_dst"] == "old"
    assert history_calls[0][1]["new_dst"] == "new"
    assert view.query_results[0]["preview"] == "new"
    assert view.shard_dst_original == "new"
    assert any("已套用 C3 DST" in msg for _, msg in notify_calls)


def test_on_query_search_all_mode_deduplicates_per_type_key(monkeypatch):
    view = CacheView.__new__(CacheView)
    view.page = FakePage()
    view.ui_busy = False

    view.tf_query_input = ft.TextField(value="abc")
    view.dd_query_mode = ft.Dropdown(value="ALL")
    view.dd_query_type = ft.Dropdown(value="ALL")
    view.query_search_hint = ft.Text(value="")

    view._last_overview_data = {
        "types": {
            "lang": {"active_shard_id": "00001"},
            "patchouli": {"active_shard_id": "00002"},
        }
    }

    view.query_results = []
    view.query_selected_result = None
    view.query_page = 1

    view._notify = lambda *args, **kwargs: None
    view._render_query_results = lambda: None
    view._render_query_detail = lambda: None

    def fake_search(cache_type, query, mode, limit):
        assert query == "abc"
        if mode == "key":
            return {"items": [{"key": "k1"}, {"key": "k2"}]}
        if mode == "dst":
            return {"items": [{"key": "k2", "preview": "dup"}, {"key": "k3", "preview": "p3"}]}
        return {"items": []}

    monkeypatch.setattr(cache_view_module, "cache_search_service", fake_search)
    monkeypatch.setattr(cache_view_module, "cache_get_entry_service", lambda ctype, key: {"dst": f"dst-{ctype}-{key}"})

    CacheView._on_query_search(view, None)

    keys = {(r["cache_type"], r["key"]) for r in view.query_results}
    assert len(keys) == 6  # 2 cache types * (k1, k2, k3)
    assert len(view.query_results) == 6
    assert view.query_selected_result is not None
    assert view.query_search_hint.value.startswith("搜尋完成：6 筆")


def test_on_query_search_requires_input(monkeypatch):
    view = CacheView.__new__(CacheView)
    view.page = FakePage()
    view.ui_busy = False

    view.tf_query_input = ft.TextField(value="")
    view.dd_query_mode = ft.Dropdown(value="ALL")
    view.dd_query_type = ft.Dropdown(value="ALL")
    view.query_search_hint = ft.Text(value="")
    view._last_overview_data = {"types": {"lang": {}}}
    view.query_results = []
    view.query_selected_result = None

    calls = []
    view._notify = lambda msg, level="info": calls.append((level, msg))
    view._render_query_results = lambda: None
    view._render_query_detail = lambda: None

    monkeypatch.setattr(cache_view_module, "cache_search_service", lambda *args, **kwargs: {"items": []})

    CacheView._on_query_search(view, None)

    assert calls and calls[0][0] == "warn"
    assert "請輸入查詢內容" in calls[0][1]
