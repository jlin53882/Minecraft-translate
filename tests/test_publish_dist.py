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


def test_publish_never_deletes_existing_files(tmp_path):
    target = tmp_path / "dist" / "App"
    _publish(tmp_path, _make_staging(tmp_path, "1"), target)
    stale = target / "lib" / "old_version_only.dll"
    stale.write_text("old", encoding="utf-8")
    _publish(tmp_path, _make_staging(tmp_path, "2"), target)
    assert stale.exists()  # 已知代價：舊版遺留檔不會被清掉


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
