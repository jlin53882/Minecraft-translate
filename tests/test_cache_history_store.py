from app.views.cache_manager import cache_history_store as hs


def test_history_append_and_load_recent(tmp_path):
    cache_root = str(tmp_path / "cache_root")
    hs.history_append_event(
        cache_root,
        "lang",
        {
            "key": "k1",
            "old_dst": "a",
            "new_dst": "b",
            "ts": "2026-03-12T23:00:00+08:00",
        },
    )
    rows = hs.history_load_recent(cache_root, "lang", "k1", limit=5)

    assert len(rows) == 1
    assert rows[0]["new_dst"] == "b"


def test_history_dirs_returns_none_when_root_missing():
    assert hs.history_dirs("", "lang") == (None, None, None)


# ---- #114：歷史紀錄不得在呼叫端（UI handler）反覆解析／整份改寫 ----

import json  # noqa: E402
import threading  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_history_memo():
    hs.clear_history_memo()
    yield
    hs.history_flush()
    hs.clear_history_memo()


def _event(key, n, ts=None):
    return {"key": key, "old_dst": f"o{n}", "new_dst": f"n{n}", "ts": ts or f"t{n:03d}"}


def test_load_recent_is_newest_first_limited_and_scoped_to_the_key(tmp_path):
    root = str(tmp_path)
    for n in range(5):
        hs.history_append_event(root, "lang", _event("a", n))
        hs.history_append_event(root, "lang", _event("b", n))
    rows = hs.history_load_recent(root, "lang", "a", limit=3)
    assert [r["new_dst"] for r in rows] == ["n4", "n3", "n2"]
    assert hs.history_load_recent(root, "lang", "missing") == []


def test_append_then_load_does_not_reparse_the_file(tmp_path, monkeypatch):
    root = str(tmp_path)
    hs.history_append_event(root, "lang", _event("a", 0))
    assert len(hs.history_load_recent(root, "lang", "a")) == 1  # 第一次載入：解析並記住

    parses = []
    real = hs._parse_index
    monkeypatch.setattr(hs, "_parse_index", lambda fp: parses.append(fp) or real(fp))
    hs.history_append_event(root, "lang", _event("a", 1))  # 附加 → 就地更新記憶
    rows = hs.history_load_recent(root, "lang", "a")
    assert [r["new_dst"] for r in rows] == ["n1", "n0"]
    assert parses == []  # 套用後立刻讀歷史，不必重新解析整個檔案


def test_external_modification_invalidates_the_memo(tmp_path):
    root = str(tmp_path)
    hs.history_append_event(root, "lang", _event("a", 0))
    hs.history_load_recent(root, "lang", "a")
    jsonl = next((tmp_path / "cache_history" / "lang" / "jsonl").glob("*.jsonl"))
    with jsonl.open("a", encoding="utf-8") as f:  # 別的程式寫入
        f.write(json.dumps(_event("a", 9)) + "\n")
    assert [r["new_dst"] for r in hs.history_load_recent(root, "lang", "a")] == [
        "n9",
        "n0",
    ]


def test_json_mirror_is_written_in_a_background_thread_in_order(tmp_path, monkeypatch):
    root = str(tmp_path)
    threads = []
    real = hs._append_mirror

    def spy(json_path, event, max_per_file):
        threads.append(threading.current_thread().name)
        return real(json_path, event, max_per_file)

    monkeypatch.setattr(hs, "_append_mirror", spy)
    for n in range(5):
        hs.history_append_event(root, "lang", _event("a", n))
    hs.history_flush()

    assert threads and all(name.startswith("history-mirror") for name in threads)
    assert threading.current_thread().name not in threads  # 不在呼叫端執行緒
    mirror = next((tmp_path / "cache_history" / "lang" / "json").glob("*.json"))
    assert [e["new_dst"] for e in json.loads(mirror.read_text(encoding="utf-8"))] == [
        f"n{n}" for n in range(5)
    ]  # 依呼叫順序


def test_jsonl_history_is_available_while_derived_mirror_is_blocked(
    tmp_path, monkeypatch
):
    root = str(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    real_append = hs._append_mirror

    def blocked_append(json_path, event, max_per_file):
        entered.set()
        assert release.wait(2)
        real_append(json_path, event, max_per_file)

    monkeypatch.setattr(hs, "_append_mirror", blocked_append)
    hs.history_append_event(root, "lang", _event("canonical", 1))
    assert entered.wait(1)
    try:
        rows = hs.history_load_recent(root, "lang", "canonical")
        assert [row["new_dst"] for row in rows] == ["n1"]
    finally:
        release.set()

    hs.history_flush(timeout=2)
    mirror = next((tmp_path / "cache_history" / "lang" / "json").glob("*.json"))
    assert json.loads(mirror.read_text(encoding="utf-8")) == [_event("canonical", 1)]


def test_json_mirror_is_truncated_to_max_per_file(tmp_path):
    json_path = tmp_path / "m.json"
    for n in range(7):
        hs._append_mirror(json_path, _event("a", n), 3)
    assert [e["new_dst"] for e in json.loads(json_path.read_text())] == [
        "n4",
        "n5",
        "n6",
    ]


def test_warm_history_index_parses_files_once(tmp_path, monkeypatch):
    root = str(tmp_path)
    hs.history_append_event(root, "lang", _event("a", 0))
    hs.history_flush()
    hs.clear_history_memo()
    assert hs.warm_history_index(root, ["lang", "missing"]) == 1

    monkeypatch.setattr(
        Path, "read_text", lambda *a, **k: pytest.fail("預熱後不應再讀檔")
    )
    assert len(hs.history_load_recent(root, "lang", "a")) == 1
