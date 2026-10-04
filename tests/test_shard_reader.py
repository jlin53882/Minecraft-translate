"""分片摘要記憶與背景預熱（#114：event loop 上的分片頁渲染不得解析大型 JSON）。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from app.views.cache_manager import shard_reader as sr


@pytest.fixture(autouse=True)
def _clean_memo():
    sr.clear_memo()
    yield
    sr.clear_memo()


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_rows_sorted_newest_first_main_file_excluded_and_active_flagged(tmp_path):
    root = tmp_path
    for i, n in ((1, 2), (2, 3), (10, 1)):
        _write(root / "lang" / f"lang_{i:05d}.json", {f"k{j}": {} for j in range(n)})
    _write(root / "lang" / "lang_cache_main.json", {"ignored": {}})

    rows = sr.shard_rows(str(root), "lang", "00002", 2500)

    assert [r["filename"] for r in rows] == [
        "lang_00010.json",
        "lang_00002.json",
        "lang_00001.json",
    ]
    assert [r["key_count"] for r in rows] == [1, 3, 2]
    assert [r["is_active"] for r in rows] == [False, True, False]
    assert all(r["capacity"] == 2500 for r in rows)
    assert sr.shard_rows(str(root), "missing", "1", 10) == []


def test_summary_is_memoized_until_the_file_changes(tmp_path, monkeypatch):
    fp = _write(tmp_path / "a.json", {"b": {}, "a": {}})
    reads = []
    real = Path.read_text

    def spy(self, *a, **k):
        reads.append(str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy)
    assert sr.shard_summary(fp) == (2, ["a", "b"])
    assert sr.shard_summary(fp) == (2, ["a", "b"])
    assert len(reads) == 1  # 第二次是記憶命中

    # 檔案被改寫（簽名改變）→ 重新讀取，舊簽名不留在記憶中
    time.sleep(0.01)
    _write(fp, {"a": {}, "b": {}, "c": {}})
    os.utime(fp, (time.time() + 5, time.time() + 5))
    assert sr.shard_summary(fp) == (3, ["a", "b", "c"])
    assert len(reads) == 2
    assert len([k for k in sr._SUMMARY_MEMO if k[0] == str(fp)]) == 1


def test_list_shards_use_index_placeholders_and_bad_json_is_zero(tmp_path):
    listing = _write(tmp_path / "l.json", [{"key": "x"}, {"nokey": 1}])
    assert sr.shard_summary(listing) == (2, ["x", "[1]"])
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert sr.shard_summary(bad) == (0, [])
    assert sr.shard_summary(tmp_path / "missing.json") == (0, [])


def test_warm_then_render_path_never_parses_json(tmp_path, monkeypatch):
    """背景預熱後，event loop 上的 rows／keys 查詢只剩記憶命中（不再 read_text／json.loads）。"""
    for i in range(3):
        _write(
            tmp_path / "lang" / f"lang_{i:05d}.json",
            {f"k{j}": {} for j in range(i + 1)},
        )
    overview = {"cache_root": str(tmp_path), "types": {"lang": {}}}
    assert sr.warm_shard_summaries(overview) == 3

    monkeypatch.setattr(
        Path, "read_text", lambda *a, **k: pytest.fail("預熱後不應再讀檔解析 JSON")
    )
    rows = sr.shard_rows(str(tmp_path), "lang", "00001", 10)
    assert [r["key_count"] for r in rows] == [3, 2, 1]
    assert sr.shard_summary(tmp_path / "lang" / "lang_00002.json")[1] == [
        "k0",
        "k1",
        "k2",
    ]


def test_raw_lru_keeps_only_recent_shards(tmp_path):
    files = [_write(tmp_path / f"{i}.json", {"k": {"dst": str(i)}}) for i in range(4)]
    for fp in files:
        assert sr.shard_raw(fp)["k"]["dst"] == fp.stem
    assert len(sr._RAW_LRU) == 2  # 只常駐最近 2 個，不會把所有分片都留在記憶體


def test_cache_view_warms_summaries_in_the_background_fetch(tmp_path, monkeypatch):
    """CacheView 載入總覽的背景工作會預熱分片摘要（渲染時才不必在 event loop 解析）。"""
    from app.views import cache_view as cv

    _write(tmp_path / "lang" / "lang_00001.json", {"k": {}})
    overview = {"cache_root": str(tmp_path), "types": {"lang": {}}}
    monkeypatch.setattr(cv, "cache_get_overview_service", lambda: overview)
    view = cv.CacheView.__new__(cv.CacheView)
    data, error, _tb = cv.CacheView._fetch_overview(view)
    assert error is None and data is overview
    assert len(sr._SUMMARY_MEMO) == 1


def test_cache_action_warms_shard_summaries_inside_the_worker(monkeypatch):
    """快取動作的背景工作結束前預熱分片摘要，finish（event loop）再渲染時不必解析 JSON。"""
    import threading

    from app.views.cache_manager import cache_actions as ca

    warmed = []
    rendered = []

    class _View:
        ui_busy = False
        page = None

        def __init__(self):
            self.overview_status = type("T", (), {"value": ""})()

        def _append_log(self, text):
            pass

        def _notify(self, *a):
            pass

        def _set_state(self, *a):
            pass

        def _refresh_overview_ui(self, data):
            pass

        def _refresh_query_type_options(self):
            pass

        def _render_query_type_shard_page(self):
            rendered.append(threading.get_ident())

        def _warm_shard_cache(self, data):
            warmed.append((threading.get_ident(), data))

    view = _View()
    ca.run_cache_action(view, "SAVING", lambda: {"cache_root": "/x", "types": {}}, "ok")
    assert warmed and warmed[0][1] == {"cache_root": "/x", "types": {}}
    assert rendered  # 預熱在渲染之前完成（同步路徑下順序即為 warm → render）
