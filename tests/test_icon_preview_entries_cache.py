"""Source identity and review-root ownership tests for Icon Preview caches."""

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.views.icon_preview.entries_cache import (
    _compute_source_identity,
    _save_entries_cache_l2,
    open_entries_cache_l2,
)
from app.views.icon_preview.mod_store import MemorySource


def _load_entries_cache_l2(source_root):
    """開啟 L2 並讀出所有模組的來源項目（None 表示快取失效）。"""
    source = open_entries_cache_l2(source_root)
    if source is None:
        return None
    return [row for modid in source.modids for row in source.load(modid)]


def _write_translation(review_root, value):
    path = review_root / "mod" / "lang" / "zh_tw.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"key": value}), encoding="utf-8")


def _write_source_language(source_root, relative_path, value):
    path = source_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"key": value}), encoding="utf-8")
    return path


def _make_view(source_root, review_root, entries_cache=None, cache_meta=None):
    """``entries_cache`` 為來源項目 dict 清單，會包成 L1 的記憶體來源。"""
    from app.views.icon_preview_view import IconPreviewView

    view = IconPreviewView.__new__(IconPreviewView)
    view.source_root = source_root
    view.review_root = review_root
    view._source_cache = (
        None if entries_cache is None else MemorySource.from_entries(entries_cache)
    )
    view.mod_page_size = 50
    view._cache_meta = cache_meta or {}
    return view


def _source_entry():
    return {
        "modid": "mod",
        "key": "key",
        "en": "English",
        "source_jar": "mod.jar",
        "icon_path": "cached-icon.png",
    }


def test_l1_cache_reuses_source_entries_but_hydrates_new_review_root(tmp_path):
    source_root = tmp_path / "mods"
    source_root.mkdir()
    (source_root / "mod.jar").write_bytes(b"jar")
    review_a = tmp_path / "review-a"
    review_b = tmp_path / "review-b"
    _write_translation(review_a, "翻譯 A")
    _write_translation(review_b, "翻譯 B")

    source_entries = [_source_entry()]
    view = _make_view(
        source_root,
        review_a,
        entries_cache=source_entries,
        cache_meta={
            "source_identity": _compute_source_identity(source_root, "jar_directory")
        },
    )

    first = view._lookup_cached_entries("jar_directory")
    assert first[0] == "L1"
    assert first[1]["mod"][0].zh_tw == "翻譯 A"

    view.review_root = review_b
    second = view._lookup_cached_entries("jar_directory")
    assert second[0] == "L1"
    assert second[1]["mod"][0].zh_tw == "翻譯 B"
    assert "zh_tw" not in view._source_cache.load("mod")[0]


def test_fresh_extracted_scan_hydrates_current_review_root(tmp_path):
    source_root = tmp_path / "source"
    source_file = source_root / "assets" / "mod" / "lang" / "en_us.json"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(json.dumps({"key": "English"}), encoding="utf-8")
    review_root = tmp_path / "review"
    _write_translation(review_root, "目前譯文")
    view = _make_view(source_root, review_root)

    entries = view._scan_entries("extracted_folder", 1)

    assert len(entries) == 1
    assert entries[0].en == "English"
    assert entries[0].zh_tw == "目前譯文"


def test_extracted_l1_cache_misses_when_language_file_metadata_changes(tmp_path):
    source_root = tmp_path / "source"
    source_file = _write_source_language(
        source_root, "assets/mod/lang/en_us.json", "Old"
    )
    identity = _compute_source_identity(source_root, "extracted_folder")
    view = _make_view(
        source_root,
        review_root=None,
        entries_cache=[_source_entry()],
        cache_meta={"source_identity": identity},
    )
    assert view._lookup_cached_entries("extracted_folder")[0] == "L1"

    old_stat = source_file.stat()
    source_file.write_text(json.dumps({"key": "New"}), encoding="utf-8")
    os.utime(
        source_file,
        ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 2_000_000_000),
    )

    new_stat = source_file.stat()
    assert new_stat.st_size == old_stat.st_size
    assert new_stat.st_mtime_ns != old_stat.st_mtime_ns
    assert view._lookup_cached_entries("extracted_folder") is None


def test_extracted_l1_cache_misses_when_language_files_are_added_or_deleted(
    tmp_path,
):
    source_root = tmp_path / "source"
    source_file = _write_source_language(
        source_root, "assets/mod/lang/en_us.json", "English"
    )
    identity = _compute_source_identity(source_root, "extracted_folder")
    view = _make_view(
        source_root,
        review_root=None,
        entries_cache=[_source_entry()],
        cache_meta={"source_identity": identity},
    )

    _write_source_language(source_root, "assets/another/lang/en_us.json", "Added")
    assert view._lookup_cached_entries("extracted_folder") is None

    view._cache_meta = {
        "source_identity": _compute_source_identity(source_root, "extracted_folder")
    }
    assert view._lookup_cached_entries("extracted_folder")[0] == "L1"
    source_file.unlink()
    assert view._lookup_cached_entries("extracted_folder") is None


def test_l2_cache_is_rehydrated_per_view_and_serializes_source_only(
    tmp_path, monkeypatch
):
    from app.views.icon_preview import entries_cache

    source_root = tmp_path / "mods"
    source_root.mkdir()
    (source_root / "mod.jar").write_bytes(b"jar")
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(entries_cache, "_get_cache_dir", lambda: cache_root)
    _save_entries_cache_l2(
        source_root,
        [SimpleNamespace(**_source_entry(), zh_tw="stale translation")],
    )

    cache_dir = next(path for path in cache_root.iterdir() if path.is_dir())
    saved = json.loads((cache_dir / "index.json").read_text(encoding="utf-8"))
    assert saved["version"] == entries_cache._CACHE_VERSION
    assert [mod[:2] for mod in saved["mods"]] == [["mod", 1]]
    shard_text = (cache_dir / saved["mods"][0][2]).read_text(encoding="utf-8")
    assert "stale translation" not in shard_text
    assert "zh_tw" not in shard_text
    assert _load_entries_cache_l2(source_root) == [_source_entry()]

    review_a = tmp_path / "review-a"
    review_b = tmp_path / "review-b"
    _write_translation(review_a, "翻譯 A")
    _write_translation(review_b, "翻譯 B")
    first_view = _make_view(source_root, review_a)
    second_view = _make_view(source_root, review_b)

    first = first_view._lookup_cached_entries("jar_directory")
    second = second_view._lookup_cached_entries("jar_directory")
    assert first[0] == second[0] == "L2"
    assert first[1]["mod"][0].zh_tw == "翻譯 A"
    assert second[1]["mod"][0].zh_tw == "翻譯 B"
    assert "zh_tw" not in _load_entries_cache_l2(source_root)[0]


def test_l2_cache_misses_when_jar_mtime_changes_without_size_change(
    tmp_path, monkeypatch
):
    from app.views.icon_preview import entries_cache

    source_root = tmp_path / "mods"
    source_root.mkdir()
    jar_path = source_root / "mod.jar"
    jar_path.write_bytes(b"old")
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(entries_cache, "_get_cache_dir", lambda: cache_root)
    _save_entries_cache_l2(source_root, [_source_entry()])
    assert _load_entries_cache_l2(source_root) is not None
    view = _make_view(
        source_root,
        review_root=None,
        entries_cache=[_source_entry()],
        cache_meta={
            "source_identity": _compute_source_identity(source_root, "jar_directory")
        },
    )
    assert view._lookup_cached_entries("jar_directory")[0] == "L1"

    old_stat = jar_path.stat()
    jar_path.write_bytes(b"new")
    os.utime(
        jar_path,
        ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 2_000_000_000),
    )
    new_stat = jar_path.stat()
    assert new_stat.st_size == old_stat.st_size
    assert new_stat.st_mtime_ns != old_stat.st_mtime_ns
    assert _load_entries_cache_l2(source_root) is None
    assert view._lookup_cached_entries("jar_directory") is None


def test_l2_cache_misses_when_jar_set_changes(tmp_path, monkeypatch):
    from app.views.icon_preview import entries_cache

    source_root = tmp_path / "mods"
    source_root.mkdir()
    first_jar = source_root / "first.jar"
    first_jar.write_bytes(b"first")
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(entries_cache, "_get_cache_dir", lambda: cache_root)

    _save_entries_cache_l2(source_root, [_source_entry()])
    (source_root / "second.jar").write_bytes(b"second")
    assert _load_entries_cache_l2(source_root) is None

    deleted_source_root = tmp_path / "mods-after-add"
    deleted_source_root.mkdir()
    (deleted_source_root / "first.jar").write_bytes(b"first")
    second_jar = deleted_source_root / "second.jar"
    second_jar.write_bytes(b"second")
    _save_entries_cache_l2(deleted_source_root, [_source_entry()])
    second_jar.unlink()
    assert _load_entries_cache_l2(deleted_source_root) is None


@pytest.mark.parametrize("failure", ["deleted", "corrupt", "length_mismatch"])
def test_l2_shard_failure_invalidates_cache_and_rebuild_recovers(
    tmp_path, monkeypatch, failure
):
    from app.views.icon_preview import entries_cache

    source_root = tmp_path / "mods"
    source_root.mkdir()
    (source_root / "mod.jar").write_bytes(b"jar")
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(entries_cache, "_get_cache_dir", lambda: cache_root)
    _save_entries_cache_l2(source_root, [_source_entry()])

    cache_dir = next(path for path in cache_root.iterdir() if path.is_dir())
    index = json.loads((cache_dir / "index.json").read_text(encoding="utf-8"))
    shard_path = cache_dir / index["mods"][0][2]
    if failure == "deleted":
        shard_path.unlink()
    elif failure == "corrupt":
        shard_path.write_text("{", encoding="utf-8")
    else:
        shard_path.write_text(
            json.dumps(
                {
                    "key": ["key"],
                    "en": [],
                    "jars": ["mod.jar"],
                    "jar": [0],
                    "icon": ["cached-icon.png"],
                }
            ),
            encoding="utf-8",
        )

    view = _make_view(source_root, review_root=None)
    assert view._lookup_cached_entries("jar_directory") is None
    assert not cache_dir.exists()

    # The normal scan path can rebuild the exact same source identity after the
    # invalid cache has been removed, and the next lookup must use the rebuilt data.
    _save_entries_cache_l2(source_root, [_source_entry()])
    recovered = view._lookup_cached_entries("jar_directory")
    assert recovered[0] == "L2"
    assert recovered[1]["mod"][0].key == "key"


def test_l2_cache_rejects_previous_schema_versions(tmp_path, monkeypatch):
    from app.views.icon_preview import entries_cache

    source_root = tmp_path / "mods"
    source_root.mkdir()
    (source_root / "mod.jar").write_bytes(b"jar")
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    monkeypatch.setattr(entries_cache, "_get_cache_dir", lambda: cache_root)
    cache_dir = cache_root / entries_cache._compute_cache_key(source_root)
    cache_dir.mkdir()
    for version in (1, 2, 5):
        (cache_dir / "index.json").write_text(
            json.dumps(
                {
                    "version": version,
                    "source_identity": _compute_source_identity(
                        source_root, "jar_directory"
                    ),
                    "mods": [],
                }
            ),
            encoding="utf-8",
        )

        assert _load_entries_cache_l2(source_root) is None


def test_l2_hit_reads_only_index_and_requested_mod_shards(tmp_path, monkeypatch):
    """快取命中時只讀 index；各模組的分片在第一次存取時才讀，且不再重複讀。"""
    from app.views.icon_preview import entries_cache
    from app.views.icon_preview.mod_store import ModEntryStore

    source_root = tmp_path / "mods"
    source_root.mkdir()
    (source_root / "mod.jar").write_bytes(b"jar")
    monkeypatch.setattr(entries_cache, "_get_cache_dir", lambda: tmp_path / "cache")
    _save_entries_cache_l2(
        source_root,
        [
            {**_source_entry(), "modid": modid, "key": f"item.{modid}.a"}
            for modid in ("alpha", "beta", "gamma")
        ],
    )

    opened = []
    real_open = open

    def tracking_open(path, *args, **kwargs):
        opened.append(Path(path).name)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)
    source = open_entries_cache_l2(source_root)
    store = ModEntryStore(source, {"item.beta.a": "乙"})
    assert opened == ["index.json"]
    assert list(store) == ["alpha", "beta", "gamma"] and len(store) == 3
    assert store["beta"][0].zh_tw == "乙"
    assert store["beta"][0].icon_path == "cached-icon.png"
    store["beta"]
    assert opened == ["index.json", "1.json"]
    assert "delta" not in store


def test_view_l2_lookup_warms_only_first_page_and_apply_does_not_read_disk(
    tmp_path, monkeypatch
):
    """L2 命中：背景只讀 index 與第一頁模組的分片；event loop 套用時不再讀檔。"""
    from app.views.icon_preview import entries_cache
    from app.views.icon_preview_view import IconPreviewView
    from tests.conftest import mock_page

    source_root = tmp_path / "mods"
    source_root.mkdir()
    (source_root / "mod.jar").write_bytes(b"jar")
    monkeypatch.setattr(entries_cache, "_get_cache_dir", lambda: tmp_path / "cache")
    _save_entries_cache_l2(
        source_root,
        [
            {**_source_entry(), "modid": f"mod{n:03d}", "key": f"item.mod{n:03d}.a"}
            for n in range(120)
        ],
    )
    view = IconPreviewView(mock_page())
    view.source_root = source_root
    view.review_root = None

    opened = []
    real_open = open

    def tracking_open(path, *args, **kwargs):
        if str(path).endswith(".json"):
            opened.append((Path(path).name, threading.get_ident()))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)
    kind, store = view._lookup_cached_entries("jar_directory")
    assert kind == "L2"
    assert [name for name, _ in opened].count("index.json") == 1
    assert len(opened) == 1 + view.mod_page_size  # index + 第一頁分片
    opened.clear()

    view._apply_cached_entries(kind, store, "jar_directory")

    import asyncio

    while view.page._tasks:  # 前後頁預載（to_thread），不在 event loop 上讀檔
        handler, args = view.page._tasks.pop(0)
        asyncio.run(handler(*args))
    assert all(tid != threading.get_ident() for _, tid in opened)
    assert len(view.list_view.controls) == view.mod_page_size
    assert view._source_cache is store.source


def test_deferred_l2_shard_failure_invalidates_and_queues_recovery(
    tmp_path, monkeypatch
):
    """第二／三頁的分片損壞也要失效來源並回到完整重掃流程。"""
    from app.views.icon_preview import entries_cache
    from app.views.icon_preview.entries_cache import ShardLoadError
    from app.views.icon_preview_view import IconPreviewView
    from tests.conftest import mock_page

    source_root = tmp_path / "mods"
    source_root.mkdir()
    (source_root / "mod.jar").write_bytes(b"jar")
    monkeypatch.setattr(entries_cache, "_get_cache_dir", lambda: tmp_path / "cache")
    rows = [
        {
            **_source_entry(),
            "modid": f"mod{n:03d}",
            "key": f"item.mod{n:03d}.a",
        }
        for n in range(120)
    ]
    _save_entries_cache_l2(source_root, rows)

    cache_dir = next(path for path in (tmp_path / "cache").iterdir() if path.is_dir())
    index = json.loads((cache_dir / "index.json").read_text(encoding="utf-8"))
    third_page_shard = cache_dir / index["mods"][80][2]
    third_page_shard.write_text("{", encoding="utf-8")

    page = mock_page()
    view = IconPreviewView(page)
    view.source_root = source_root
    view.review_root = None
    kind, store = view._lookup_cached_entries("jar_directory")
    assert kind == "L2"

    reloads = []
    view._on_load_clicked = lambda _event: reloads.append(True)
    with pytest.raises(ShardLoadError):
        store["mod080"]

    assert not cache_dir.exists()
    assert "mod080" not in store._hydrated
    page._run_all_tasks()
    assert reloads == [True]
