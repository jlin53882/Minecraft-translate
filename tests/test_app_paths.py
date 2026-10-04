"""資料根目錄決定規則（#137）：原始碼、PyInstaller、Nuitka、環境變數覆蓋。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from translation_tool.utils import app_paths

REPO_ROOT = Path(app_paths.__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(app_paths.DATA_DIR_ENV, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    app_paths._migrated_roots.clear()
    yield
    app_paths._migrated_roots.clear()


def test_source_mode_uses_repo_root():
    assert app_paths.get_data_root() == REPO_ROOT
    assert not app_paths.is_frozen()


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(app_paths.DATA_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert app_paths.get_data_root() == tmp_path.resolve()


def test_pyinstaller_uses_executable_dir(monkeypatch, tmp_path):
    exe = tmp_path / "App.exe"
    exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert app_paths.is_frozen()
    assert app_paths.get_data_root() == tmp_path.resolve()


def test_nuitka_uses_argv0_dir(monkeypatch, tmp_path):
    exe = tmp_path / "App.exe"
    exe.write_text("")
    monkeypatch.setitem(vars(app_paths), "__compiled__", object())
    monkeypatch.setattr(sys, "argv", [str(exe)])
    assert app_paths.is_frozen()
    assert app_paths.get_data_root() == tmp_path.resolve()


def test_data_paths_follow_data_root(tmp_path):
    """config / checkpoint / icon 快取路徑都應落在資料根目錄之下。

    以子行程執行，避免 reload 模組污染其他測試。
    """
    import os
    import subprocess

    code = (
        "from translation_tool.utils import config_manager as c;"
        "from translation_tool.core import lm_translator as t;"
        "from app.services_impl import config_service as s;"
        "from app import icon_index;"
        "print(c.CONFIG_PATH);print(s.CONFIG_PATH);print(t.CHECKPOINT_FILE)"
    )
    env = dict(os.environ, **{app_paths.DATA_DIR_ENV: str(tmp_path)})
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    root = tmp_path.resolve()
    assert Path(out[0]) == root / "config.json"
    assert Path(out[1]) == root / "config.json"
    assert Path(out[2]) == root / "logs" / "translation_checkpoint.json"


def _run_in_data_root(code: str, data_root: Path, cwd: Path) -> list[str]:
    """在子行程執行（MCT_DATA_DIR 於啟動前設定），cwd 與 repo 都不同於資料根目錄。"""
    import os
    import subprocess

    env = dict(
        os.environ,
        **{app_paths.DATA_DIR_ENV: str(data_root), "PYTHONPATH": str(REPO_ROOT)},
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


def test_icon_cache_paths_follow_data_root(monkeypatch, tmp_path):
    """icon index / jar icon / model index / L2 快取都必須在 <資料根目錄>/.icon_cache 之下。

    若有人改回 Path(__file__) 或 cwd，這裡會失敗。
    """
    from app import icon_index
    from app.views import icon_preview_view

    root = tmp_path / "data"
    monkeypatch.setenv(app_paths.DATA_DIR_ENV, str(root))
    monkeypatch.chdir(tmp_path)  # cwd 不得被當成基準
    cache_root = root.resolve() / ".icon_cache"

    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    index_path = icon_index.get_index_path(mods_dir)

    assert index_path.parent == cache_root / "icon_index"
    assert icon_preview_view._get_icon_cache_dir() == cache_root / "jar_icons"
    assert icon_preview_view._get_model_index_cache_dir() == cache_root / "model_index"
    assert icon_preview_view._get_cache_dir() == cache_root
    # 不得寫到 repo 或 cwd
    assert not (REPO_ROOT / ".icon_cache" / "icon_index" / index_path.name).exists()
    assert not (tmp_path / ".icon_cache").exists()


def test_error_log_lives_under_data_root_logs(tmp_path):
    """errors_<日期>.log 必須在 <資料根目錄>/logs，而不是 repo、cwd 或模組目錄。"""
    from datetime import datetime

    root = tmp_path / "data"
    root.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    name = f"errors_{datetime.now().astimezone().strftime('%Y-%m-%d')}.log"
    repo_log = REPO_ROOT / "logs" / name
    repo_before = repo_log.stat().st_size if repo_log.exists() else None

    _run_in_data_root(
        "from translation_tool.utils.exceptions import _log_error_to_file;"
        "_log_error_to_file(ValueError('data-root-marker'), 'probe_func')",
        root,
        cwd,
    )

    log_file = root.resolve() / "logs" / name
    assert log_file.exists()
    assert "data-root-marker" in log_file.read_text(encoding="utf-8")
    assert not (cwd / "logs").exists()
    after = repo_log.stat().st_size if repo_log.exists() else None
    assert after == repo_before, "不得寫進 repo 的 logs/"


def test_error_log_creates_missing_nested_data_root(tmp_path):
    """MCT_DATA_DIR 指向尚不存在的巢狀目錄時，仍要建立 logs/ 並寫出錯誤記錄。"""
    from datetime import datetime

    root = tmp_path / "not-created" / "nested" / "root"
    assert not root.exists()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    name = f"errors_{datetime.now().astimezone().strftime('%Y-%m-%d')}.log"

    _run_in_data_root(
        "from translation_tool.utils.exceptions import _log_error_to_file;"
        "_log_error_to_file(RuntimeError('nested-root-marker'), 'probe_func')",
        root,
        cwd,
    )

    log_file = root.resolve() / "logs" / name
    assert log_file.is_file()
    assert "nested-root-marker" in log_file.read_text(encoding="utf-8")
    assert "probe_func" in log_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# app/ 與 data/ 分離
# ---------------------------------------------------------------------------


def _frozen_exe(monkeypatch, exe: Path) -> None:
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))


def test_split_layout_uses_sibling_data_dir(monkeypatch, tmp_path):
    _frozen_exe(monkeypatch, tmp_path / "root" / "app" / "App.exe")
    assert app_paths.is_split_layout()
    assert app_paths.get_data_root() == tmp_path / "root" / "data"
    # 資源（唯讀）留在 app/，不跟著搬到 data/
    assert app_paths.get_resource_root() == (tmp_path / "root" / "app").resolve()


def test_flat_layout_keeps_old_behaviour(monkeypatch, tmp_path):
    """exe 不在 app/ 資料夾：維持舊版平面式（資料與程式同一資料夾），不污染上一層。"""
    _frozen_exe(monkeypatch, tmp_path / "MT" / "App.exe")
    assert not app_paths.is_split_layout()
    assert app_paths.get_data_root() == (tmp_path / "MT").resolve()
    assert app_paths.get_resource_root() == (tmp_path / "MT").resolve()
    assert not (tmp_path / "data").exists()


def test_source_mode_resource_root_is_repo_root():
    assert app_paths.get_resource_root() == REPO_ROOT
    assert (REPO_ROOT / "config.example.json").is_file()
    assert (
        REPO_ROOT / "translation_tool" / "core" / "resource_pack_version.json"
    ).is_file()


def test_env_override_beats_split_layout(monkeypatch, tmp_path):
    _frozen_exe(monkeypatch, tmp_path / "root" / "app" / "App.exe")
    override = tmp_path / "elsewhere"
    monkeypatch.setenv(app_paths.DATA_DIR_ENV, str(override))
    assert app_paths.get_data_root() == override.resolve()
    assert not (tmp_path / "root" / "data").exists()  # 覆蓋時不觸發遷移


def test_first_start_migrates_legacy_data_into_data_dir(monkeypatch, tmp_path):
    root = tmp_path / "root"
    _frozen_exe(monkeypatch, root / "app" / "App.exe")
    legacy = {
        "config.json": "legacy-config",
        "replace_rules.json": "legacy-rules",
        "logs/2026/app.log": "log",
        "快取資料/lang/a.json": "{}",
        "學名資料庫/s.json": "{}",
        ".icon_cache/jar_icons/a.png": "png",
        "custom_translators/t.py": "x",
    }
    for rel, content in legacy.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    unrelated = root / "readme.txt"
    unrelated.write_text("keep", encoding="utf-8")

    data_root = app_paths.get_data_root()

    for rel, content in legacy.items():
        assert (data_root / rel).read_text(encoding="utf-8") == content, rel
        assert not (root / rel).exists(), f"{rel} 應已搬走"
    assert unrelated.read_text(encoding="utf-8") == "keep"  # 未知檔案不碰


def test_migration_never_overwrites_existing_data(tmp_path):
    root = tmp_path / "root"
    data = root / "data"
    data.mkdir(parents=True)
    (data / "config.json").write_text("new-config", encoding="utf-8")
    (root / "config.json").write_text("old-config", encoding="utf-8")
    (root / "logs").mkdir()
    (root / "logs" / "a.log").write_text("log", encoding="utf-8")

    moved = app_paths.migrate_legacy_data(root, data)

    assert moved == ["logs"]
    assert (data / "config.json").read_text(encoding="utf-8") == "new-config"
    assert (root / "config.json").read_text(encoding="utf-8") == "old-config"
    assert (data / "logs" / "a.log").exists()


def test_migration_is_idempotent_and_runs_once(monkeypatch, tmp_path):
    root = tmp_path / "root"
    _frozen_exe(monkeypatch, root / "app" / "App.exe")
    (root / "config.json").write_text("legacy", encoding="utf-8")

    calls = []
    real = app_paths.migrate_legacy_data
    monkeypatch.setattr(
        app_paths, "migrate_legacy_data", lambda *a: calls.append(a) or real(*a)
    )
    app_paths.get_data_root()
    app_paths.get_data_root()
    assert len(calls) == 1
    assert (root / "data" / "config.json").read_text(encoding="utf-8") == "legacy"


def test_migration_failure_does_not_break_startup(monkeypatch, tmp_path):
    root = tmp_path / "root"
    _frozen_exe(monkeypatch, root / "app" / "App.exe")
    (root / "config.json").write_text("legacy", encoding="utf-8")

    def boom(src, dest):
        raise OSError("locked")

    monkeypatch.setattr(app_paths.shutil, "move", boom)
    assert app_paths.get_data_root() == root / "data"  # 不丟例外
    assert (root / "config.json").exists()  # 失敗的項目留在原處，下次再試
