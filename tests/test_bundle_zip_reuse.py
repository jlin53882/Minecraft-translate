"""打包 ZIP 沿用／重建契約與逐檔進度（#165 方向 2、3）。"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from translation_tool.core import output_bundler
from translation_tool.core.output_bundler import (
    BUNDLE_STATE_SUFFIX,
    bundle_outputs_generator,
)


@pytest.fixture
def src(tmp_path) -> Path:
    root = tmp_path / "src"
    for i in range(3):
        lang = root / f"mod{i}/assets/m{i}/lang/zh_tw.json"
        lang.parent.mkdir(parents=True)
        lang.write_text(json.dumps({f"k{k}": f"v{i}{k}" for k in range(50)}))
    return root


@pytest.fixture
def zip_path(tmp_path) -> Path:
    return tmp_path / "out.zip"


def run(src, zip_path, **kw):
    kw.setdefault("description", "d")
    return list(bundle_outputs_generator(str(src), str(zip_path), **kw))


def reused(updates) -> bool:
    return "沿用" in updates[-1]["log"]


def state_file(zip_path: Path) -> Path:
    return Path(str(zip_path) + BUNDLE_STATE_SUFFIX)


def stamp(zip_path: Path):
    st = zip_path.stat()
    return zip_path.read_bytes(), st.st_mtime_ns


def test_identical_input_reuses_zip_untouched(src, zip_path):
    assert not reused(run(src, zip_path))
    before = stamp(zip_path)
    assert reused(run(src, zip_path))
    assert stamp(zip_path) == before  # byte 與 mtime 皆不變


def test_sidecar_never_enters_the_archive(src, zip_path):
    run(src, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        assert not any("bundle-state" in n for n in zf.namelist())
    assert state_file(zip_path).exists()
    assert not Path(str(zip_path) + ".tmp").exists()


def edit_file(src, zip_path):
    (src / "mod0/assets/m0/lang/zh_tw.json").write_text('{"a": "changed"}')


def add_file(src, zip_path):
    (src / "mod0/assets/m0/lang/new.json").write_text("{}")


def remove_file(src, zip_path):
    (src / "mod1/assets/m1/lang/zh_tw.json").unlink()


def rename_file(src, zip_path):
    d = src / "mod2/assets/m2/lang"
    (d / "zh_tw.json").rename(d / "zh_cn.json")


def case_rename(src, zip_path):
    d = src / "mod2/assets/m2/lang"
    (d / "zh_tw.json").rename(d / "ZH_TW.json")


def change_pack_mcmeta(src, zip_path):
    (src / "pack.mcmeta").write_text('{"pack": {"pack_format": 1}}')


def change_pack_png(src, zip_path):
    (src / "pack.png").write_bytes(b"\x89PNG-new")


def delete_zip(src, zip_path):
    zip_path.unlink()


def corrupt_zip(src, zip_path):
    data = bytearray(zip_path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    zip_path.write_bytes(bytes(data))


def replace_zip(src, zip_path):
    zip_path.write_bytes(b"not a zip")


def state_missing(src, zip_path):
    state_file(zip_path).unlink()


def state_garbage(src, zip_path):
    state_file(zip_path).write_text("{ not json")


def state_wrong_version(src, zip_path):
    p = state_file(zip_path)
    state = json.loads(p.read_text())
    state["version"] = 999
    p.write_text(json.dumps(state))


@pytest.mark.parametrize(
    "mutate",
    [
        edit_file,
        add_file,
        remove_file,
        rename_file,
        case_rename,
        change_pack_mcmeta,
        change_pack_png,
        delete_zip,
        corrupt_zip,
        replace_zip,
        state_missing,
        state_garbage,
        state_wrong_version,
    ],
)
def test_any_change_triggers_rebuild(src, zip_path, mutate):
    (src / "pack.mcmeta").write_text('{"pack": {"pack_format": 15}}')
    (src / "pack.png").write_bytes(b"\x89PNG-old")
    run(src, zip_path)
    mutate(src, zip_path)

    updates = run(src, zip_path)

    assert not reused(updates)
    assert not updates[-1].get("error")
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.testzip() is None
    assert reused(run(src, zip_path))  # 重建後狀態已生效


def test_description_and_format_change_rebuild(src, zip_path):
    run(src, zip_path)
    assert not reused(run(src, zip_path, description="other"))
    assert not reused(run(src, zip_path, description="other", min_format=5))


def test_extra_folders_change_rebuilds(src, zip_path, tmp_path):
    extra = tmp_path / "extra"
    (extra / "sub").mkdir(parents=True)
    (extra / "sub" / "a.txt").write_text("1")
    run(src, zip_path, extra_folders=[str(extra)])
    assert reused(run(src, zip_path, extra_folders=[str(extra)]))
    (extra / "sub" / "a.txt").write_text("2")
    assert not reused(run(src, zip_path, extra_folders=[str(extra)]))
    assert not reused(run(src, zip_path))  # 移除 extra 也要重建


def test_pack_image_change_rebuilds(src, zip_path, tmp_path):
    img = tmp_path / "icon.png"
    img.write_bytes(b"one")
    run(src, zip_path, pack_image_path=str(img))
    assert reused(run(src, zip_path, pack_image_path=str(img)))
    img.write_bytes(b"two")
    assert not reused(run(src, zip_path, pack_image_path=str(img)))


def test_compression_level_change_rebuilds(src, zip_path, monkeypatch):
    run(src, zip_path)
    monkeypatch.setattr(output_bundler, "ZIP_COMPRESS_LEVEL", 9)
    assert not reused(run(src, zip_path))


def test_same_size_same_mtime_edit_is_detected(src, zip_path):
    f = src / "mod0/assets/m0/lang/zh_tw.json"
    run(src, zip_path)
    st = f.stat()
    data = f.read_bytes()
    f.write_bytes(data.replace(b"v00", b"v01", 1))
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert f.stat().st_size == st.st_size

    assert not reused(run(src, zip_path))


def test_force_rebuild_ignores_matching_state(src, zip_path):
    run(src, zip_path)
    assert not reused(run(src, zip_path, force_rebuild=True))


def test_build_exception_commits_no_state(src, zip_path, monkeypatch):
    run(src, zip_path)
    previous = zip_path.read_bytes()
    edit_file(src, zip_path)

    def boom(*a, **k):
        raise OSError("disk full")
        yield  # pragma: no cover

    monkeypatch.setattr(output_bundler, "_iter_add_folder_to_zip", boom)
    updates = run(src, zip_path)
    assert updates[-1].get("error")
    assert zip_path.exists() and zip_path.read_bytes() == previous  # 舊 ZIP 保留
    assert not state_file(zip_path).exists()
    assert not Path(str(zip_path) + ".tmp").exists()
    monkeypatch.undo()

    # 失敗後再跑不得誤沿用半成品，且會成功重建
    updates = run(src, zip_path)
    assert not reused(updates) and not updates[-1].get("error")


def test_cancel_commits_no_state_and_leaves_no_tmp(src, zip_path):
    gen = bundle_outputs_generator(str(src), str(zip_path), description="d")
    for _ in range(6):
        next(gen)
    gen.close()

    assert not state_file(zip_path).exists()
    assert not Path(str(zip_path) + ".tmp").exists()
    assert not reused(run(src, zip_path))


def test_cancel_during_rebuild_keeps_previous_zip_but_invalidates_state(src, zip_path):
    run(src, zip_path)
    edit_file(src, zip_path)
    gen = bundle_outputs_generator(str(src), str(zip_path), description="d")
    for _ in range(6):
        next(gen)
    gen.close()

    assert not state_file(zip_path).exists()
    assert not reused(run(src, zip_path))


def test_reuse_path_content_equals_fresh_rebuild(src, zip_path, tmp_path):
    run(src, zip_path)
    run(src, zip_path)  # 沿用
    fresh = tmp_path / "fresh.zip"
    run(src, fresh)

    def contents(p):
        with zipfile.ZipFile(p) as zf:
            return {n: zf.read(n) for n in zf.namelist()}

    assert contents(zip_path) == contents(fresh)


def test_progress_is_reported_per_file_in_a_single_large_folder(tmp_path, zip_path):
    root = tmp_path / "big"
    big = root / "only/assets/m/lang"
    big.mkdir(parents=True)
    for i in range(200):
        (big / f"f{i}.json").write_text("{}")

    updates = run(root, zip_path)
    file_updates = [u for u in updates if "個檔案（" in u.get("log", "")]

    assert len(file_updates) >= 10  # 單一資料夾期間也持續回報
    progress = [u["progress"] for u in file_updates]
    assert progress == sorted(progress)
    assert "200 / 200" in file_updates[-1]["log"]
    assert updates[-1]["progress"] == 1.0


def test_failed_first_build_leaves_no_zip_or_tmp(src, zip_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
        yield  # pragma: no cover

    monkeypatch.setattr(output_bundler, "_iter_add_folder_to_zip", boom)
    assert run(src, zip_path)[-1].get("error")
    assert not zip_path.exists() and not Path(str(zip_path) + ".tmp").exists()


def test_replace_failure_keeps_previous_zip(src, zip_path, monkeypatch):
    run(src, zip_path)
    previous = zip_path.read_bytes()
    edit_file(src, zip_path)

    def bad_replace(*a, **k):
        raise PermissionError("locked")

    monkeypatch.setattr(output_bundler.os, "replace", bad_replace)
    updates = run(src, zip_path)
    monkeypatch.undo()

    assert updates[-1].get("error")
    assert zip_path.read_bytes() == previous
    assert not state_file(zip_path).exists()
    assert not Path(str(zip_path) + ".tmp").exists()


def test_state_commit_failure_keeps_new_zip_and_next_run_rebuilds(
    src, zip_path, monkeypatch
):
    run(src, zip_path)
    previous = zip_path.read_bytes()
    edit_file(src, zip_path)

    def bad_commit(*a, **k):
        raise OSError("sidecar write failed")

    monkeypatch.setattr(output_bundler, "_commit_state", bad_commit)
    updates = run(src, zip_path)
    monkeypatch.undo()

    assert not updates[-1].get("error")
    assert "無法保存打包狀態" in updates[-1]["log"]
    assert zip_path.exists() and zip_path.read_bytes() != previous  # 新 ZIP 保留
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.testzip() is None
    assert not state_file(zip_path).exists()
    assert not reused(run(src, zip_path))  # 無有效狀態 → 重建
    assert reused(run(src, zip_path))


def test_touched_zip_with_same_content_is_still_reused(src, zip_path):
    run(src, zip_path)
    st = zip_path.stat()
    os.utime(zip_path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))

    assert reused(run(src, zip_path))  # 內容相同，只有 mtime 不同


def test_zip_mutation_keeping_size_and_mtime_is_detected(src, zip_path):
    run(src, zip_path)
    st = zip_path.stat()
    data = bytearray(zip_path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    zip_path.write_bytes(bytes(data))
    os.utime(zip_path, ns=(st.st_atime_ns, st.st_mtime_ns))  # 還原 mtime
    assert zip_path.stat().st_size == st.st_size

    updates = run(src, zip_path)  # 不需要 force_rebuild

    assert not reused(updates)
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.testzip() is None


def test_progress_never_goes_backwards_with_pack_and_root_files(tmp_path, zip_path):
    root = tmp_path / "p"
    for i in range(5):
        d = root / f"m{i}/assets/m/lang"
        d.mkdir(parents=True)
        for k in range(40):
            (d / f"f{k}.json").write_text("{}")
    (root / "pack.mcmeta").write_text('{"pack": {}}')
    (root / "pack.png").write_bytes(b"\x89PNG")
    (root / "readme.txt").write_text("x")
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "e.txt").write_text("e")

    updates = run(root, zip_path, extra_folders=[str(extra)])
    values = [u["progress"] for u in updates if u["progress"] >= 0.15]

    assert values == sorted(values)
    assert updates[-1]["progress"] == 1.0
