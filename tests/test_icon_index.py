"""Regression tests for the persistent prebuilt icon-index workflow."""

import os
import zipfile

import pytest

from app import icon_index


def test_saved_index_is_invalidated_when_jar_metadata_changes(tmp_path, monkeypatch):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    jar = mods_dir / "example-1.0.jar"
    jar.write_bytes(b"first")
    monkeypatch.setattr(icon_index, "get_data_root", lambda: tmp_path / "data")

    icon_index.save_icon_index(mods_dir, {"item.example.apple": "icon://old"})
    assert icon_index.load_icon_index(mods_dir) == {"item.example.apple": "icon://old"}

    old_stat = jar.stat()
    jar.write_bytes(b"other")  # same filename and byte length
    os.utime(jar, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 2_000_000_000))

    assert icon_index.load_icon_index(mods_dir) is None


def test_build_and_save_icon_index_creates_loadable_index(tmp_path, monkeypatch):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    jar = mods_dir / "example-1.0.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr("assets/example/lang/en_us.lang", "item.example.apple=Apple\n")
        archive.writestr(
            "assets/example/models/item/apple.json",
            '{"textures":{"layer0":"example:item/apple"}}',
        )
        archive.writestr("assets/example/textures/item/apple.png", b"png")

    monkeypatch.setattr(icon_index, "get_data_root", lambda: tmp_path / "data")
    from app.views.icon_preview import icon_cache

    monkeypatch.setattr(
        icon_cache, "_get_model_index_cache_dir", lambda: tmp_path / "model-index"
    )
    monkeypatch.setattr(
        icon_index,
        "load_config",
        lambda: {"translator": {"parallel_execution_workers": 1}},
    )

    result = icon_index.build_and_save_icon_index(mods_dir)

    assert "item.example.apple" in result
    assert icon_index.load_icon_index(mods_dir) == result


def test_build_and_save_refuses_to_persist_if_jars_change_during_build(
    tmp_path, monkeypatch
):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    jar = mods_dir / "example.jar"
    jar.write_bytes(b"before")
    monkeypatch.setattr(icon_index, "get_data_root", lambda: tmp_path / "data")

    def mutate_during_build(_mods_dir, progress_cb=None):
        jar.write_bytes(b"changed")
        return {"item.example.apple": "icon://test"}

    monkeypatch.setattr(icon_index, "build_icon_index", mutate_during_build)
    with pytest.raises(RuntimeError, match="changed while"):
        icon_index.build_and_save_icon_index(mods_dir)
    assert not list((tmp_path / "data").rglob("*.json"))
