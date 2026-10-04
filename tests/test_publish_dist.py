"""tools/publish_dist.py：app/ 完全取代、data/ 永遠保留（PR #152 review）。"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "publish_dist", REPO_ROOT / "tools" / "publish_dist.py"
)
publish_dist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publish_dist)

EXE = "App.exe"
LAUNCHER = "App.bat"


def _staging(root: Path, files: dict[str, str]) -> Path:
    staging = root / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    for rel, content in files.items():
        path = staging / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return staging


def _publish(root: Path, staging: Path, target: Path) -> None:
    example = root / "config.example.json"
    example.write_text('{"v": "example-new"}', encoding="utf-8")
    rules = root / "replace_rules.json"
    rules.write_text('{"rules": "default"}', encoding="utf-8")
    publish_dist.publish(
        staging,
        target,
        EXE,
        update_files=[(example, "config.example.json")],
        seed_files=[(example, "config.json"), (rules, "replace_rules.json")],
        launcher_name=LAUNCHER,
    )


def _files(base: Path) -> dict[str, str]:
    return {
        p.relative_to(base).as_posix(): p.read_text(encoding="utf-8")
        for p in base.rglob("*")
        if p.is_file()
    }


def test_first_publish_creates_split_layout(tmp_path):
    target = tmp_path / "dist" / "App"
    _publish(tmp_path, _staging(tmp_path, {EXE: "e1", "lib/a.dll": "a"}), target)

    assert (target / "app" / EXE).read_text(encoding="utf-8") == "e1"
    assert (target / "app" / "lib" / "a.dll").exists()
    assert (target / "app" / "config.example.json").exists()
    # 使用者資料在 data/，且首次建立
    assert (target / "data" / "config.json").exists()
    assert (target / "data" / "replace_rules.json").exists()
    # 程式端檔案不會出現在 data/
    assert not (target / "data" / EXE).exists()
    launcher = (target / LAUNCHER).read_text(encoding="utf-8")
    assert "app\\App.exe" in launcher and launcher.startswith("@echo off")
    assert not (target / "app.new").exists() and not (target / "app.old").exists()


def test_upgrade_replaces_app_completely(tmp_path):
    """版本 B 已移除的 DLL / PYD / 套件不得殘留；結果等價於乾淨 build。"""
    target = tmp_path / "dist" / "App"
    v1 = {
        EXE: "exe-1",
        "lib/module_a.pyd": "a1",
        "lib/old_dependency.dll": "dll1",
        "lib/package_x/__init__.py": "x1",
        "lib/kept.dll": "k1",
    }
    _publish(tmp_path, _staging(tmp_path, v1), target)

    v2 = {EXE: "exe-2", "lib/kept.dll": "k2", "lib/package_y/__init__.py": "y2"}
    _publish(tmp_path, _staging(tmp_path, v2), target)

    actual = _files(target / "app")
    actual.pop("config.example.json")
    assert actual == v2


def test_upgrade_keeps_data_unchanged(tmp_path):
    target = tmp_path / "dist" / "App"
    _publish(tmp_path, _staging(tmp_path, {EXE: "e1"}), target)

    user_files = {
        "config.json": '{"lm_translator": {"keys": ["AIzaUSER"]}}',
        "replace_rules.json": '{"rules": "user-edited"}',
        "logs/2026-10-04/app.log": "log line",
        "快取資料/lang/shard_0001.json": '{"k": "v"}',
        "學名資料庫/species.json": "{}",
        ".icon_cache/jar_icons/a.png": "png",
        "custom_translators/mine.py": "print(1)",
    }
    for rel, content in user_files.items():
        path = target / "data" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    _publish(tmp_path, _staging(tmp_path, {EXE: "e2"}), target)

    for rel, content in user_files.items():
        assert (target / "data" / rel).read_text(encoding="utf-8") == content, rel
    assert (target / "app" / EXE).read_text(encoding="utf-8") == "e2"


def test_failed_build_does_not_touch_existing_install(tmp_path):
    target = tmp_path / "dist" / "App"
    _publish(tmp_path, _staging(tmp_path, {EXE: "e1"}), target)
    (target / "data" / "config.json").write_text("user", encoding="utf-8")
    before = _files(target)

    broken = tmp_path / "broken_staging"
    broken.mkdir()  # 沒有 exe：模擬 build 失敗
    with pytest.raises(publish_dist.PublishError):
        publish_dist.publish(broken, target, EXE)
    with pytest.raises(publish_dist.PublishError):
        publish_dist.publish(tmp_path / "missing", target, EXE)

    assert _files(target) == before


def test_missing_source_or_bad_dest_fails_before_any_change(tmp_path):
    target = tmp_path / "dist" / "App"
    staging = _staging(tmp_path, {EXE: "e1"})
    with pytest.raises(publish_dist.PublishError):
        publish_dist.publish(
            staging, target, EXE, seed_files=[(tmp_path / "nope.json", "x.json")]
        )
    example = tmp_path / "e.json"
    example.write_text("{}", encoding="utf-8")
    with pytest.raises(publish_dist.PublishError):
        publish_dist.publish(
            staging, target, EXE, seed_files=[(example, "../escape.json")]
        )
    assert not target.exists()


def test_locked_app_dir_aborts_and_keeps_old_version(tmp_path, monkeypatch):
    """程式執行中（目錄被鎖）：替換失敗，舊版與 data 完整保留。"""
    target = tmp_path / "dist" / "App"
    _publish(tmp_path, _staging(tmp_path, {EXE: "e1", "lib/a.dll": "a1"}), target)
    (target / "data" / "config.json").write_text("user", encoding="utf-8")
    before = _files(target)

    real_rename = Path.rename

    def locked(self, dest):
        if self.name == "app" and Path(dest).name == "app.old":
            raise PermissionError("locked by running process")
        return real_rename(self, dest)

    monkeypatch.setattr(Path, "rename", locked)
    with pytest.raises(publish_dist.PublishError, match="正在執行"):
        _publish(tmp_path, _staging(tmp_path, {EXE: "e2"}), target)
    monkeypatch.undo()

    assert _files(target) == before
    assert not (target / "app.new").exists()


def test_failure_after_old_moved_restores_old_app(tmp_path, monkeypatch):
    target = tmp_path / "dist" / "App"
    _publish(tmp_path, _staging(tmp_path, {EXE: "e1"}), target)
    before = _files(target)

    real_rename = Path.rename

    def fail_second(self, dest):
        if self.name == "app.new":
            raise OSError("disk error")
        return real_rename(self, dest)

    monkeypatch.setattr(Path, "rename", fail_second)
    with pytest.raises(publish_dist.PublishError):
        _publish(tmp_path, _staging(tmp_path, {EXE: "e2"}), target)
    monkeypatch.undo()

    assert (target / "app" / EXE).read_text(encoding="utf-8") == "e1"
    assert _files(target) == before


def test_leftover_temp_dirs_from_interrupted_run_are_cleaned(tmp_path):
    target = tmp_path / "dist" / "App"
    _publish(tmp_path, _staging(tmp_path, {EXE: "e1"}), target)
    (target / "app.new").mkdir()
    (target / "app.new" / "junk").write_text("x", encoding="utf-8")
    (target / "app.old").mkdir()
    _publish(tmp_path, _staging(tmp_path, {EXE: "e2"}), target)
    assert not (target / "app.new").exists() and not (target / "app.old").exists()
    assert (target / "app" / EXE).read_text(encoding="utf-8") == "e2"


def test_legacy_flat_install_files_are_never_deleted(tmp_path):
    """舊版平面式：根目錄的舊檔與資料不刪（資料由新版首次啟動搬進 data/）。"""
    target = tmp_path / "dist" / "App"
    target.mkdir(parents=True)
    (target / EXE).write_text("old-exe", encoding="utf-8")
    (target / "config.json").write_text("legacy-config", encoding="utf-8")
    (target / "logs").mkdir()
    (target / "logs" / "a.log").write_text("log", encoding="utf-8")

    _publish(tmp_path, _staging(tmp_path, {EXE: "new"}), target)

    assert (target / EXE).read_text(encoding="utf-8") == "old-exe"
    assert (target / "config.json").read_text(encoding="utf-8") == "legacy-config"
    assert (target / "logs" / "a.log").exists()
    assert (target / "app" / EXE).read_text(encoding="utf-8") == "new"


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
    assert "/mir" not in text.lower()


# ---------------------------------------------------------------------------
# 完整升級生命週期：舊版平面式 → publish → 新版首次啟動 migration
# ---------------------------------------------------------------------------


def _start_new_version(monkeypatch, target: Path) -> Path:
    """模擬使用者啟動 target/app/App.exe，回傳新版實際使用的資料根目錄。"""
    from translation_tool.utils import app_paths

    exe = target / "app" / EXE
    assert exe.is_file()
    monkeypatch.setattr(app_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_paths.sys, "executable", str(exe))
    app_paths._migrated_roots.clear()
    try:
        return app_paths.get_data_root()
    finally:
        app_paths._migrated_roots.clear()


def test_legacy_flat_upgrade_preserves_user_config_and_rules(tmp_path, monkeypatch):
    """舊版 config.json / replace_rules.json 不得被預設 seed 擋住而無法遷移。"""
    target = tmp_path / "dist" / "App"
    target.mkdir(parents=True)
    (target / EXE).write_text("old-exe", encoding="utf-8")
    (target / "config.json").write_text("USER-CONFIG", encoding="utf-8")
    (target / "replace_rules.json").write_text("USER-RULES", encoding="utf-8")
    (target / "logs").mkdir()
    (target / "logs" / "a.log").write_text("USER-LOG", encoding="utf-8")
    (target / "快取資料").mkdir()
    (target / "快取資料" / "c.json").write_text("USER-CACHE", encoding="utf-8")

    _publish(tmp_path, _staging(tmp_path, {EXE: "new-exe"}), target)

    # publish 不得預先建立會擋住 migration 的預設檔
    assert not (target / "data" / "config.json").exists()
    assert not (target / "data" / "replace_rules.json").exists()

    data_root = _start_new_version(monkeypatch, target)

    assert data_root == target / "data"
    assert (data_root / "config.json").read_text(encoding="utf-8") == "USER-CONFIG"
    assert (data_root / "replace_rules.json").read_text(
        encoding="utf-8"
    ) == "USER-RULES"
    assert (data_root / "logs" / "a.log").read_text(encoding="utf-8") == "USER-LOG"
    assert (data_root / "快取資料" / "c.json").read_text(
        encoding="utf-8"
    ) == "USER-CACHE"
    assert not (target / "config.json").exists()
    assert not (target / "replace_rules.json").exists()
    # 舊程式檔不刪（留給使用者確認新版正常後手動清除）
    assert (target / EXE).read_text(encoding="utf-8") == "old-exe"


def test_legacy_with_only_config_still_seeds_missing_rules(tmp_path, monkeypatch):
    target = tmp_path / "dist" / "App"
    target.mkdir(parents=True)
    (target / "config.json").write_text("USER-CONFIG", encoding="utf-8")

    _publish(tmp_path, _staging(tmp_path, {EXE: "new"}), target)
    data_root = _start_new_version(monkeypatch, target)

    assert (data_root / "config.json").read_text(encoding="utf-8") == "USER-CONFIG"
    # 舊安裝沒有規則檔：發佈時補預設
    assert (data_root / "replace_rules.json").exists()


def test_fresh_install_still_gets_default_seed(tmp_path, monkeypatch):
    target = tmp_path / "dist" / "App"
    _publish(tmp_path, _staging(tmp_path, {EXE: "new"}), target)
    data_root = _start_new_version(monkeypatch, target)
    assert (data_root / "config.json").read_text(
        encoding="utf-8"
    ) == '{"v": "example-new"}'
    assert (data_root / "replace_rules.json").exists()


def test_republish_after_migration_never_overwrites_migrated_data(
    tmp_path, monkeypatch
):
    target = tmp_path / "dist" / "App"
    target.mkdir(parents=True)
    (target / "config.json").write_text("USER-CONFIG", encoding="utf-8")
    _publish(tmp_path, _staging(tmp_path, {EXE: "v1"}), target)
    _start_new_version(monkeypatch, target)  # 遷移完成
    (target / "data" / "config.json").write_text("EDITED-AFTER", encoding="utf-8")

    _publish(tmp_path, _staging(tmp_path, {EXE: "v2"}), target)

    assert (target / "data" / "config.json").read_text(
        encoding="utf-8"
    ) == "EDITED-AFTER"
