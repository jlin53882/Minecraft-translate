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
