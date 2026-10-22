"""config.example.json 必須由設定 schema 產生（#134），不可手動漂移。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from translation_tool.utils.config_manager import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]


def _generator():
    spec = importlib.util.spec_from_file_location(
        "gen_config_example", ROOT / "tools" / "gen_config_example.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_example_file_is_exactly_what_the_generator_produces():
    gen = _generator()
    assert (ROOT / "config.example.json").read_text(encoding="utf-8") == gen.render(), (
        "config.example.json 與 schema 不一致，請執行 python tools/gen_config_example.py"
    )


def test_example_equals_default_config():
    example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    assert example == DEFAULT_CONFIG
    assert example["lm_translator"]["keys"] == []


def test_first_run_config_from_example_is_complete(tmp_path):
    """第一次啟動：把範本複製成 config.json，不需要任何補欄位。"""
    from translation_tool.utils.config_manager import sync_missing_config_keys

    cfg = tmp_path / "config.json"
    cfg.write_text(
        (ROOT / "config.example.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    before = cfg.read_text(encoding="utf-8")
    sync_missing_config_keys(cfg)
    assert cfg.read_text(encoding="utf-8") == before


def test_check_mode_passes_when_current_and_fails_on_drift(tmp_path, monkeypatch):
    """``--check`` 與 schema 一致時回傳 0；範本過期（或不存在）時回傳 1 且不寫檔。"""
    gen = _generator()
    assert gen.main(["--check"]) == 0

    stale = tmp_path / "config.example.json"
    stale.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(gen, "OUT", stale)
    assert gen.main(["--check"]) == 1
    assert stale.read_text(encoding="utf-8") == "{}\n"

    monkeypatch.setattr(gen, "OUT", tmp_path / "missing.json")
    assert gen.main(["--check"]) == 1
