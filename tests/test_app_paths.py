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
