"""tools/publish_dist.py：重新打包不得刪除使用者資料（PR #152 review）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "publish_dist", REPO_ROOT / "tools" / "publish_dist.py"
)
publish_dist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publish_dist)

EXE = "App.exe"


def _make_staging(root: Path, version: str) -> Path:
    staging = root / "staging"
    (staging / "lib").mkdir(parents=True, exist_ok=True)
    (staging / EXE).write_text(f"exe-{version}", encoding="utf-8")
    (staging / "lib" / "core.dll").write_text(f"dll-{version}", encoding="utf-8")
    return staging


def _sources(root: Path) -> dict[str, Path]:
    example = root / "config.example.json"
    example.write_text('{"v": "example-new"}', encoding="utf-8")
    rules = root / "replace_rules.json"
    rules.write_text('{"rules": "default"}', encoding="utf-8")
    return {"example": example, "rules": rules}


def _publish(root: Path, staging: Path, target: Path) -> None:
    src = _sources(root)
    publish_dist.publish(
        staging,
        target,
        EXE,
        update_files=[(src["example"], "config.example.json")],
        seed_files=[
            (src["example"], "config.json"),
            (src["rules"], "replace_rules.json"),
        ],
    )


def test_first_publish_creates_exe_and_seed_files(tmp_path):
    target = tmp_path / "dist" / "App"
    _publish(tmp_path, _make_staging(tmp_path, "1"), target)
    assert (target / EXE).read_text(encoding="utf-8") == "exe-1"
    assert (target / "lib" / "core.dll").exists()
    assert (target / "config.json").exists()
    assert (target / "replace_rules.json").exists()


def test_second_publish_keeps_user_data_unchanged(tmp_path):
    target = tmp_path / "dist" / "App"
    _publish(tmp_path, _make_staging(tmp_path, "1"), target)

    # 模擬使用者執行後產生 / 編輯的資料
    user_files = {
        "config.json": '{"lm_translator": {"keys": ["AIzaUSER"]}}',
        "replace_rules.json": '{"rules": "user-edited"}',
        "logs/2026-10-04/app.log": "log line",
        "快取資料/lang/shard_0001.json": '{"k": "v"}',
        "學名資料庫/species.json": "{}",
        ".icon_cache/jar_icons/a.png": "png",
        "zh_tw_generated/out.json": "{}",
    }
    for rel, content in user_files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # 第二次 build：新版程式檔
    _publish(tmp_path, _make_staging(tmp_path, "2"), target)

    for rel, content in user_files.items():
        assert (target / rel).read_text(encoding="utf-8") == content, rel
    # 程式檔與隨附範本有更新
    assert (target / EXE).read_text(encoding="utf-8") == "exe-2"
    assert (target / "lib" / "core.dll").read_text(encoding="utf-8") == "dll-2"
    assert "example-new" in (target / "config.example.json").read_text(encoding="utf-8")


def _make_staging_files(root: Path, files: dict[str, str]) -> Path:
    import shutil

    staging = root / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    for rel, content in files.items():
        path = staging / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return staging


def test_removed_packaged_files_are_deleted_on_upgrade(tmp_path):
    """版本 B 已移除的 DLL / PYD / 套件，不得留在正式資料夾形成混合版本。"""
    target = tmp_path / "dist" / "App"
    v1 = {
        EXE: "exe-1",
        "lib/module_a.pyd": "a1",
        "lib/old_dependency.dll": "dll1",
        "lib/package_x/__init__.py": "x1",
        "lib/package_x/old_part.py": "x-old",
        "lib/kept.dll": "k1",
    }
    _publish(tmp_path, _make_staging_files(tmp_path, v1), target)
    assert (target / "lib" / "old_dependency.dll").exists()

    # 版本 B：移除 module_a、old_dependency、package_x 整個套件；kept.dll 更新
    v2 = {EXE: "exe-2", "lib/kept.dll": "k2", "lib/package_y/__init__.py": "y2"}
    _publish(tmp_path, _make_staging_files(tmp_path, v2), target)

    assert not (target / "lib" / "module_a.pyd").exists()
    assert not (target / "lib" / "old_dependency.dll").exists()
    assert not (target / "lib" / "package_x").exists()  # 變空的資料夾一併修剪
    assert (target / "lib" / "kept.dll").read_text(encoding="utf-8") == "k2"
    assert (target / "lib" / "package_y" / "__init__.py").exists()
    # 結果與乾淨 build 等價（除 manifest 與 seed 檔案）
    actual = {
        p.relative_to(target).as_posix()
        for p in target.rglob("*")
        if p.is_file()
        and p.name
        not in (
            publish_dist.MANIFEST_NAME,
            "config.json",
            "replace_rules.json",
            "config.example.json",
        )
    }
    assert actual == set(v2)


def test_user_data_survives_while_stale_packaged_files_are_removed(tmp_path):
    target = tmp_path / "dist" / "App"
    _publish(
        tmp_path,
        _make_staging_files(tmp_path, {EXE: "e1", "lib/old.dll": "o"}),
        target,
    )
    user_files = {
        "config.json": "user-config",
        "replace_rules.json": "user-rules",
        "logs/a.log": "log",
        "快取資料/x.json": "{}",
        "學名資料庫/s.json": "{}",
        ".icon_cache/a.png": "png",
        "zh_tw_generated/out.json": "{}",
        # 使用者把檔案放進 packaged 資料夾：不在 manifest，不得被刪
        "lib/user_note.txt": "mine",
    }
    for rel, content in user_files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    _publish(tmp_path, _make_staging_files(tmp_path, {EXE: "e2"}), target)

    assert not (target / "lib" / "old.dll").exists()
    for rel, content in user_files.items():
        assert (target / rel).read_text(encoding="utf-8") == content, rel
    assert (target / "lib").is_dir()  # 內含使用者檔案，資料夾不得被修剪


def test_first_publish_without_manifest_does_not_guess_stale_files(tmp_path):
    """沒有舊 manifest（舊安裝升級）：無法判斷，什麼都不刪（已知限制，見文件）。"""
    target = tmp_path / "dist" / "App"
    target.mkdir(parents=True)
    legacy = target / "lib" / "legacy.dll"
    legacy.parent.mkdir()
    legacy.write_text("old", encoding="utf-8")
    _publish(tmp_path, _make_staging_files(tmp_path, {EXE: "e1"}), target)
    assert legacy.exists()
    assert (target / publish_dist.MANIFEST_NAME).is_file()


def test_tampered_manifest_cannot_delete_protected_or_outside_paths(tmp_path):
    import json

    target = tmp_path / "dist" / "App"
    _publish(tmp_path, _make_staging_files(tmp_path, {EXE: "e1"}), target)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    (target / "logs").mkdir()
    (target / "logs" / "a.log").write_text("log", encoding="utf-8")
    (target / "config.json").write_text("cfg", encoding="utf-8")
    (target / publish_dist.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "files": [
                    "config.json",
                    "logs/a.log",
                    "../../outside.txt",
                    str(outside),
                    "",
                ]
            }
        ),
        encoding="utf-8",
    )

    _publish(tmp_path, _make_staging_files(tmp_path, {EXE: "e2"}), target)

    assert outside.read_text(encoding="utf-8") == "keep"
    assert (target / "logs" / "a.log").read_text(encoding="utf-8") == "log"
    assert (target / "config.json").read_text(encoding="utf-8") == "cfg"


def test_corrupt_manifest_deletes_nothing(tmp_path):
    target = tmp_path / "dist" / "App"
    _publish(
        tmp_path,
        _make_staging_files(tmp_path, {EXE: "e1", "lib/old.dll": "o"}),
        target,
    )
    (target / publish_dist.MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    _publish(tmp_path, _make_staging_files(tmp_path, {EXE: "e2"}), target)
    assert (target / "lib" / "old.dll").exists()


def test_failed_build_does_not_touch_target(tmp_path):
    target = tmp_path / "dist" / "App"
    _publish(tmp_path, _make_staging(tmp_path, "1"), target)
    (target / "config.json").write_text("user", encoding="utf-8")
    before = {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}

    broken = tmp_path / "broken_staging"
    broken.mkdir()  # 沒有 exe：模擬 build 失敗
    with pytest.raises(publish_dist.PublishError):
        publish_dist.publish(broken, target, EXE)
    with pytest.raises(publish_dist.PublishError):
        publish_dist.publish(tmp_path / "missing", target, EXE)

    after = {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert before == after


def test_missing_source_file_fails_before_any_copy(tmp_path):
    target = tmp_path / "dist" / "App"
    staging = _make_staging(tmp_path, "1")
    with pytest.raises(publish_dist.PublishError):
        publish_dist.publish(
            staging, target, EXE, seed_files=[(tmp_path / "nope.json", "x.json")]
        )
    assert not target.exists()


def test_cli_returns_nonzero_on_broken_staging(tmp_path, capsys):
    broken = tmp_path / "s"
    broken.mkdir()
    code = publish_dist.main(
        ["--staging", str(broken), "--target", str(tmp_path / "t"), "--exe", EXE]
    )
    assert code == 1
    assert not (tmp_path / "t").exists()


def test_build_script_has_no_destructive_update_of_target():
    """script-level 回歸：build_exe.bat 不得 rmdir 正式資料夾，且必須走 publish_dist。"""
    text = (REPO_ROOT / "tools" / "build_exe.bat").read_text(encoding="utf-8")
    assert "publish_dist.py" in text
    for line in text.splitlines():
        stripped = line.strip().lower()
        if "rmdir" in stripped:
            assert "staging_dir" in stripped, f"只允許清 staging：{line}"
    assert "robocopy" not in text.lower() or "/mir" not in text.lower()
