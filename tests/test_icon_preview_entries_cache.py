"""Source identity and review-root ownership tests for Icon Preview caches."""

import json
import os
from types import SimpleNamespace

from app.views.icon_preview.entries_cache import (
    _compute_source_identity,
    _load_entries_cache_l2,
    _save_entries_cache_l2,
)


def _write_translation(review_root, value):
    path = review_root / "mod" / "lang" / "zh_tw.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"key": value}), encoding="utf-8")


def _make_view(source_root, review_root, entries_cache=None, cache_meta=None):
    from app.views.icon_preview_view import IconPreviewView

    view = IconPreviewView.__new__(IconPreviewView)
    view.source_root = source_root
    view.review_root = review_root
    view._entries_cache = entries_cache
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
    assert first[1][0].zh_tw == "翻譯 A"

    view.review_root = review_b
    second = view._lookup_cached_entries("jar_directory")
    assert second[0] == "L1"
    assert second[1][0].zh_tw == "翻譯 B"
    assert view._entries_cache is source_entries
    assert "zh_tw" not in view._entries_cache[0]


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

    cache_file = next(cache_root.glob("*.json"))
    saved = json.loads(cache_file.read_text(encoding="utf-8"))
    assert saved["version"] == entries_cache._CACHE_VERSION
    assert "zh_tw" not in saved["entries"][0]
    assert set(saved["entries"][0]) == {
        "modid",
        "key",
        "en",
        "source_jar",
        "icon_path",
    }

    review_a = tmp_path / "review-a"
    review_b = tmp_path / "review-b"
    _write_translation(review_a, "翻譯 A")
    _write_translation(review_b, "翻譯 B")
    first_view = _make_view(source_root, review_a)
    second_view = _make_view(source_root, review_b)

    first = first_view._lookup_cached_entries("jar_directory")
    second = second_view._lookup_cached_entries("jar_directory")
    assert first[0] == second[0] == "L2"
    assert first[1][0].zh_tw == "翻譯 A"
    assert second[1][0].zh_tw == "翻譯 B"
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


def test_l2_cache_rejects_previous_schema_versions(tmp_path, monkeypatch):
    from app.views.icon_preview import entries_cache

    source_root = tmp_path / "mods"
    source_root.mkdir()
    (source_root / "mod.jar").write_bytes(b"jar")
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    monkeypatch.setattr(entries_cache, "_get_cache_dir", lambda: cache_root)
    cache_file = cache_root / f"{entries_cache._compute_cache_key(source_root)}.json"
    for version in (1, 2):
        cache_file.write_text(
            json.dumps(
                {"version": version, "source_root": str(source_root), "entries": []}
            ),
            encoding="utf-8",
        )

        assert _load_entries_cache_l2(source_root) is None
