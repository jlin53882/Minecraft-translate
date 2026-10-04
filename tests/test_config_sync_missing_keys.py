"""config.example.json / DEFAULT_CONFIG 新增的設定，要主動補進使用者的 config.json。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.utils import config_manager as cm


@pytest.fixture
def example(tmp_path, monkeypatch):
    """用小型範本取代真正的 DEFAULT_CONFIG / example，讓測試可預期。"""
    template = {
        "translator": {
            "output_dir_name": "zh_tw_generated",
            "new_flag": True,
            "nested": {"a": 1, "b": {"deep_new": "x"}},
            "workers_list": [1, 2],
        },
        "lm_translator": {
            "temperature": 0.3,
            "max_input_token_budget": 6000,
            "keys": ["YOUR_GEMINI_API_KEY_1"],
            "models": {"gemini-2.5-flash": {"enabled": True}},
        },
        "extractor": {"target_language": "zh_tw", "skip_zh_cn_extract": False},
        "brand_new_section": {"x": 1},
    }
    monkeypatch.setattr(cm, "DEFAULT_CONFIG", template)
    monkeypatch.setattr(cm, "load_config_example", dict)
    monkeypatch.setattr(
        cm, "DEPRECATED_CONFIG_KEYS", frozenset({"extractor.target_language"})
    )
    cm.clear_config_cache()
    yield template
    cm.clear_config_cache()


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_adds_missing_keys_but_never_changes_user_values(tmp_path, example):
    cfg = tmp_path / "config.json"
    _write(
        cfg,
        {
            "translator": {"output_dir_name": "MY_OUT", "new_flag": False},
            "lm_translator": {"temperature": 0, "keys": ["AIzaUSER"]},
            "extractor": {"skip_zh_cn_extract": True},
        },
    )

    added = cm.sync_missing_config_keys(cfg)
    result = _read(cfg)

    # 使用者的值完全不變（含 False / 0）
    assert result["translator"]["output_dir_name"] == "MY_OUT"
    assert result["translator"]["new_flag"] is False
    assert result["lm_translator"]["temperature"] == 0
    assert result["lm_translator"]["keys"] == ["AIzaUSER"]
    assert result["extractor"]["skip_zh_cn_extract"] is True
    # 缺少的欄位被補上，巢狀結構完整
    assert result["lm_translator"]["max_input_token_budget"] == 6000
    assert result["translator"]["nested"] == {"a": 1, "b": {"deep_new": "x"}}
    assert result["brand_new_section"] == {"x": 1}
    assert result["translator"]["workers_list"] == [1, 2]
    assert "translator.new_flag" not in added  # 使用者已有，不是「補上」
    assert "lm_translator.max_input_token_budget" in added
    assert "brand_new_section" in added


def test_never_touches_keys_models_or_deprecated(tmp_path, example):
    cfg = tmp_path / "config.json"
    _write(cfg, {"lm_translator": {"temperature": 0.5}, "extractor": {}})

    cm.sync_missing_config_keys(cfg)
    result = _read(cfg)

    assert "keys" not in result["lm_translator"]  # 不把佔位金鑰寫進使用者檔案
    assert "models" not in result["lm_translator"]  # 使用者自訂名單
    assert "target_language" not in result["extractor"]  # deprecated
    assert result["extractor"]["skip_zh_cn_extract"] is False  # 一般欄位照補


def test_does_not_add_models_user_removed(tmp_path, example):
    cfg = tmp_path / "config.json"
    _write(cfg, {"lm_translator": {"models": {"my-model": {"enabled": True}}}})
    cm.sync_missing_config_keys(cfg)
    assert _read(cfg)["lm_translator"]["models"] == {"my-model": {"enabled": True}}


def test_user_value_of_different_type_is_kept(tmp_path, example):
    cfg = tmp_path / "config.json"
    _write(cfg, {"translator": {"nested": "user-string"}})
    cm.sync_missing_config_keys(cfg)
    assert _read(cfg)["translator"]["nested"] == "user-string"


def test_is_idempotent_and_does_not_rewrite_when_nothing_missing(tmp_path, example):
    cfg = tmp_path / "config.json"
    _write(cfg, {"translator": {"output_dir_name": "A"}})
    assert cm.sync_missing_config_keys(cfg)  # 第一次有補
    before = cfg.read_bytes()
    mtime = cfg.stat().st_mtime_ns
    assert cm.sync_missing_config_keys(cfg) == []
    assert cfg.read_bytes() == before
    assert cfg.stat().st_mtime_ns == mtime


def test_backup_created_only_when_changes_are_made(tmp_path, example):
    cfg = tmp_path / "config.json"
    original = {"translator": {"output_dir_name": "A"}}
    _write(cfg, original)
    cm.sync_missing_config_keys(cfg)
    assert _read(tmp_path / "config.json.pre-sync.bak") == original

    clean = tmp_path / "other.json"
    _write(clean, cm.deep_merge(example, {}))
    cm.sync_missing_config_keys(clean)
    cm.sync_missing_config_keys(clean)
    # 沒有缺少欄位 → 不留備份
    assert not (tmp_path / "other.json.pre-sync.bak").exists()


def test_noop_without_config_or_with_invalid_json(tmp_path, example):
    missing = tmp_path / "config.json"
    assert cm.sync_missing_config_keys(missing) == []
    assert not missing.exists()  # 新安裝由合併機制提供預設值，不替使用者建檔

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cm.sync_missing_config_keys(bad) == []
    assert bad.read_text(encoding="utf-8") == "{not json"

    not_dict = tmp_path / "list.json"
    not_dict.write_text("[1, 2]", encoding="utf-8")
    assert cm.sync_missing_config_keys(not_dict) == []
    assert not_dict.read_text(encoding="utf-8") == "[1, 2]"


def test_write_failure_leaves_user_file_unchanged(tmp_path, example, monkeypatch):
    cfg = tmp_path / "config.json"
    _write(cfg, {"translator": {"output_dir_name": "A"}})
    before = cfg.read_bytes()
    monkeypatch.setattr(cm, "save_config", lambda *a, **k: False)
    assert cm.sync_missing_config_keys(cfg) == []
    assert cfg.read_bytes() == before


def test_no_backup_means_no_change(tmp_path, example, monkeypatch):
    cfg = tmp_path / "config.json"
    _write(cfg, {"translator": {"output_dir_name": "A"}})
    before = cfg.read_bytes()

    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(cm.shutil, "copy2", boom)
    assert cm.sync_missing_config_keys(cfg) == []
    assert cfg.read_bytes() == before


def test_real_defaults_sync_makes_user_file_complete_and_valid(tmp_path):
    """用真正的 DEFAULT_CONFIG：舊版（缺很多欄位）的 config.json 同步後可正常載入。"""
    cfg = tmp_path / "config.json"
    _write(
        cfg,
        {
            "lm_translator": {"keys": ["AIza" + "A" * 35]},
            "translator": {"output_dir_name": "MY_OUT"},
        },
    )
    added = cm.sync_missing_config_keys(cfg)
    assert added
    result = _read(cfg)
    assert result["translator"]["output_dir_name"] == "MY_OUT"
    assert result["lm_translator"]["keys"] == ["AIza" + "A" * 35]
    # 同步後的檔案與「只靠合併」得到的有效設定一致
    merged = cm.load_config(cfg)
    assert merged["translator"]["output_dir_name"] == "MY_OUT"
    assert cm.sync_missing_config_keys(cfg) == []
