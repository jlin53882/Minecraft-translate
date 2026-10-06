"""輸出 ZIP 放在來源資料夾內（預設路徑）時，打包不得把自己的產物當來源。"""

import zipfile

from translation_tool.core.output_bundler import (
    BUNDLE_STATE_SUFFIX,
    bundle_outputs_generator,
)


def _make_source(tmp_path):
    src = tmp_path / "src"
    (src / "mod_a" / "assets").mkdir(parents=True)
    (src / "mod_a" / "assets" / "lang.json").write_text('{"a": "b"}', encoding="utf-8")
    (src / "notes.txt").write_text("root file", encoding="utf-8")
    return src


def _run(src, zip_path, **kw):
    return list(bundle_outputs_generator(str(src), str(zip_path), **kw))


def _names(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return set(zf.namelist())


def _no_artifacts(names, zip_name):
    assert zip_name not in names
    assert zip_name + ".tmp" not in names
    assert zip_name + BUNDLE_STATE_SUFFIX not in names
    assert not any(n.endswith(".tmp") for n in names)


def test_zip_inside_source_root_is_not_packed_into_itself(tmp_path):
    src = _make_source(tmp_path)
    zip_path = src / "可使用翻譯.zip"

    updates = _run(src, zip_path)
    assert not any(u.get("error") for u in updates)
    _no_artifacts(_names(zip_path), zip_path.name)

    # 第二次：輸出 ZIP 與狀態檔已在來源資料夾，內容不得變大、也不得混入自己
    _run(src, zip_path, force_rebuild=True)
    names = _names(zip_path)
    _no_artifacts(names, zip_path.name)
    assert "notes.txt" in names


def test_unchanged_source_reuses_zip_even_when_zip_lives_inside(tmp_path):
    """輸出 ZIP／狀態檔在來源內不得讓指紋每次都不同，否則永遠無法沿用。"""
    src = _make_source(tmp_path)
    zip_path = src / "out.zip"
    _run(src, zip_path)
    second = _run(src, zip_path)
    assert any("沿用" in u.get("log", "") for u in second)


def test_stale_tmp_zip_from_failed_run_is_ignored(tmp_path):
    """上次失敗留下的 .tmp 在來源內：不得被掃描或打包。"""
    src = _make_source(tmp_path)
    zip_path = src / "out.zip"
    (src / "out.zip.tmp").write_bytes(b"leftover from a failed run")

    updates = _run(src, zip_path)
    assert not any(u.get("error") for u in updates)
    _no_artifacts(_names(zip_path), zip_path.name)


def test_zip_inside_a_source_subfolder_is_excluded(tmp_path):
    src = _make_source(tmp_path)
    zip_path = src / "mod_a" / "out.zip"
    _run(src, zip_path)
    _run(src, zip_path, force_rebuild=True)
    assert "mod_a/out.zip" not in _names(zip_path)


def test_zip_inside_an_extra_folder_is_excluded(tmp_path):
    src = _make_source(tmp_path)
    extra = tmp_path / "extra"
    (extra / "pack_x").mkdir(parents=True)
    (extra / "pack_x" / "f.txt").write_text("x", encoding="utf-8")
    zip_path = extra / "out.zip"
    _run(src, zip_path, extra_folders=[str(extra)])
    _run(src, zip_path, extra_folders=[str(extra)], force_rebuild=True)
    names = _names(zip_path)
    assert "out.zip" not in names
    assert "f.txt" in names


def test_stray_tmp_and_state_files_of_other_zips_are_ignored(tmp_path):
    """別的輸出檔名留下的 *.zip.tmp／狀態檔（Windows 實測 A5）也不得被打包。"""
    src = _make_source(tmp_path)
    (src / "other.zip.tmp").write_bytes(b"leftover")
    (src / "mod_a" / "old.zip.tmp").write_bytes(b"leftover in subfolder")
    (src / "other.zip.bundle-state.json").write_text("{}", encoding="utf-8")
    (src / "other.zip.bundle-state.json.tmp").write_text("{}", encoding="utf-8")
    zip_path = tmp_path / "out" / "result.zip"
    zip_path.parent.mkdir()

    _run(src, zip_path)

    names = _names(zip_path)
    assert not any(
        n.endswith((".zip.tmp", ".bundle-state.json", ".json.tmp")) for n in names
    )
    assert "notes.txt" in names and "mod_a/assets/lang.json" in names
