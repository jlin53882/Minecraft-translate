"""
Unit tests for config three-layer fallback mechanism.

三層 fallback（priority: user > example > default）：
  config.json  — 用戶實際值（Layer 1，最高優先）
  config.example.json — 文件預設值（Layer 2）
  DEFAULT_CONFIG — 程式碼 fallback（Layer 3，最終保底，唯一真相來源）

測試覆蓋：
1. 只有 config.json → 使用 user 值
2. config.json 缺欄位，有 config.example.json → 使用 example 值
3. config.json 和 config.example.json 都缺 → 使用 DEFAULT_CONFIG
4. 三層都有 → user 優先於 example 優先於 default
5. config.example.json 格式錯誤 → 忽略 example，使用 default
6. config.json 格式錯誤 → 回退到 default
7. get_default() 與 get_default_block() helper 正確性
8. lm_translator.models 不做 deep merge（使用者資料）
"""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
import tempfile
import os

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation_tool.utils.config_manager import (
    load_config,
    get_default,
    get_default_block,
    DEFAULT_CONFIG,
)
import translation_tool.utils.config_manager as cm


# =============================================================================
# Helper: set up fake config.json + optional config.example.json in tmp_path
# =============================================================================

class _MultiPatch:
    """Context manager for multiple simultaneous patches."""

    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        return [p.__enter__() for p in self._patches]

    def __exit__(self, *args):
        for p in reversed(self._patches):
            p.__exit__(*args)


def _patch_config_files(tmp_path, user_cfg=None, example_cfg=None):
    """Patch CONFIG_PATH and EXAMPLE_PATH to tmp_path, create fake files."""
    cfg_path = tmp_path / "config.json"
    ex_path = tmp_path / "config.example.json"

    if user_cfg is not None:
        cfg_path.write_text(json.dumps(user_cfg), encoding="utf-8")
    if example_cfg is not None:
        ex_path.write_text(json.dumps(example_cfg), encoding="utf-8")

    return _MultiPatch([
        patch.object(cm, "CONFIG_PATH", cfg_path),
        patch.object(cm, "EXAMPLE_PATH", ex_path),
    ])


# =============================================================================
# Layer 1: config.json only (no example, no default override)
# =============================================================================

class TestLayer1UserConfigOnly:
    """當 config.json 存在且有值時，應使用該值。"""

    def test_user_value_takes_priority(self, tmp_path):
        """config.json 的值優先於 default 和 example。"""
        user_cfg = {"translator": {"parallel_execution_workers": 99}}
        with _patch_config_files(tmp_path, user_cfg, {}):
            cfg = load_config()
        assert cfg["translator"]["parallel_execution_workers"] == 99

    def test_missing_config_json_returns_default(self, tmp_path):
        """config.json 不存在時，回退到 deep_merge(default, example)。"""
        ex_path = tmp_path / "config.example.json"
        ex_path.write_text("{}", encoding="utf-8")
        with patch.object(cm, "CONFIG_PATH", tmp_path / "nonexistent.json"), \
             patch.object(cm, "EXAMPLE_PATH", ex_path):
            cfg = load_config()
        assert "translator" in cfg
        assert "lang_merger" in cfg

    def test_corrupt_config_json_returns_default(self, tmp_path):
        """config.json 格式錯誤時，回退到 default + example。"""
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("{ invalid json }", encoding="utf-8")
        ex_path = tmp_path / "config.example.json"
        ex_path.write_text("{}", encoding="utf-8")
        with patch.object(cm, "CONFIG_PATH", cfg_path), \
             patch.object(cm, "EXAMPLE_PATH", ex_path):
            cfg = load_config()
        assert "translator" in cfg


# =============================================================================
# Layer 2: config.example.json fills missing user fields
# =============================================================================

class TestLayer2ExampleFillsMissing:
    """當 config.json 缺欄位時，config.example.json 補上。"""

    def test_example_fills_missing_top_level_key(self, tmp_path):
        """config.json 沒有 jar_extractor，example 有 → 從 example 取。"""
        user_cfg = {"translator": {"parallel_execution_workers": 4}}
        example_cfg = {"jar_extractor": {"lang_codes": ["en_us", "zh_cn", "zh_tw"]}}
        with _patch_config_files(tmp_path, user_cfg, example_cfg):
            cfg = load_config()
        assert cfg.get("jar_extractor") == {"lang_codes": ["en_us", "zh_cn", "zh_tw"]}

    def test_example_fills_missing_nested_key(self, tmp_path):
        """config.json 沒有 extractor.target_language，example 有 → 從 example 取。"""
        user_cfg = {"extractor": {"output_folder_names": {"lang_extract": "_自訂輸出"}}}
        example_cfg = {
            "extractor": {
                "target_language": ["zh_tw"],
                "skip_zh_cn_extract": False,
            }
        }
        with _patch_config_files(tmp_path, user_cfg, example_cfg):
            cfg = load_config()
        assert cfg["extractor"]["target_language"] == ["zh_tw"]
        assert cfg["extractor"]["output_folder_names"]["lang_extract"] == "_自訂輸出"

    def test_example_fills_lang_merger_fields(self, tmp_path):
        """lang_merger 新欄位（process_zh_cn_files 等）從 example 補。"""
        user_cfg = {"lang_merger": {"pending_folder_name": "待翻譯"}}
        example_cfg = {
            "lang_merger": {
                "process_zh_cn_files": True,
                "skip_zh_cn_when_only_process_lang": False,
                "patchouli_effective_translation_threshold": 0.5,
            }
        }
        with _patch_config_files(tmp_path, user_cfg, example_cfg):
            cfg = load_config()
        assert cfg["lang_merger"]["process_zh_cn_files"] is True
        assert cfg["lang_merger"]["skip_zh_cn_when_only_process_lang"] is False
        assert cfg["lang_merger"]["patchouli_effective_translation_threshold"] == 0.5
        assert cfg["lang_merger"]["pending_folder_name"] == "待翻譯"


# =============================================================================
# Layer 3: DEFAULT_CONFIG as final fallback
# =============================================================================

class TestLayer3DefaultFallback:
    """當 config.json 和 config.example.json 都缺欄位時，用 DEFAULT_CONFIG。"""

    def test_default_fills_when_both_user_and_example_missing(self, tmp_path):
        """user 和 example 都沒有 translator.parallel_execution_workers → 用 default。"""
        with _patch_config_files(tmp_path, {}, {}):
            cfg = load_config()
        assert cfg["translator"]["parallel_execution_workers"] == 4

    def test_all_top_level_keys_present(self, tmp_path):
        """load_config() 回傳的 dict 應包含 DEFAULT_CONFIG 所有頂層 key。"""
        with _patch_config_files(tmp_path, {}, {}):
            cfg = load_config()
        for key in DEFAULT_CONFIG:
            assert key in cfg, f"Missing top-level key: {key}"


# =============================================================================
# Priority: user > example > default
# =============================================================================

class TestThreeLayerPriority:
    """驗證三層 priority：user 覆蓋 example 覆蓋 default。"""

    def test_user_overrides_example(self, tmp_path):
        """user 與 example 都有值時，user 優先。"""
        user_cfg = {"translator": {"parallel_execution_workers": 99}}
        example_cfg = {"translator": {"parallel_execution_workers": 4}}
        with _patch_config_files(tmp_path, user_cfg, example_cfg):
            cfg = load_config()
        assert cfg["translator"]["parallel_execution_workers"] == 99

    def test_example_overrides_default(self, tmp_path):
        """example 有值，user 沒有時，example 優先於 default。"""
        user_cfg = {}
        example_cfg = {"translator": {"parallel_execution_workers": 7}}
        with _patch_config_files(tmp_path, user_cfg, example_cfg):
            cfg = load_config()
        assert cfg["translator"]["parallel_execution_workers"] == 7

    def test_nested_priority(self, tmp_path):
        """巢狀 dict 也能正確三層覆蓋。"""
        user_cfg = {
            "lm_translator": {
                "temperature": 0.9,
                "initial_batch_size_patchouli": 500,
            }
        }
        example_cfg = {
            "lm_translator": {
                "temperature": 0.3,
                "initial_batch_size_ftb": 200,
            }
        }
        with _patch_config_files(tmp_path, user_cfg, example_cfg):
            cfg = load_config()
        assert cfg["lm_translator"]["temperature"] == 0.9
        assert cfg["lm_translator"]["initial_batch_size_patchouli"] == 500
        assert cfg["lm_translator"]["initial_batch_size_ftb"] == 200


# =============================================================================
# Example file errors
# =============================================================================

class TestExampleFileErrors:
    """config.example.json 格式錯誤時的 fallback 行為。"""

    def test_corrupt_example_ignored_uses_default(self, tmp_path):
        """config.example.json 格式錯誤時，視同不存在。"""
        user_cfg = {}
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps(user_cfg), encoding="utf-8")
        ex_path = tmp_path / "config.example.json"
        ex_path.write_text("{ invalid json", encoding="utf-8")
        with patch.object(cm, "CONFIG_PATH", cfg_path), \
             patch.object(cm, "EXAMPLE_PATH", ex_path):
            cfg = load_config()
        assert cfg["translator"]["parallel_execution_workers"] == 4

    def test_missing_example_uses_default(self, tmp_path):
        """config.example.json 不存在時，用 default 補欄位。"""
        user_cfg = {}
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps(user_cfg), encoding="utf-8")
        with patch.object(cm, "CONFIG_PATH", cfg_path), \
             patch.object(cm, "EXAMPLE_PATH", tmp_path / "nonexistent.json"):
            cfg = load_config()
        assert cfg["translator"]["parallel_execution_workers"] == 4


# =============================================================================
# lm_translator.models special handling
# =============================================================================

class TestLmModelsSpecialHandling:
    """lm_translator.models 不做 deep merge（視為使用者資料）。"""

    def test_user_models_replace_example_models(self, tmp_path):
        """lm_translator.models 完全由 user 控制，不做 deep merge。"""
        user_cfg = {
            "lm_translator": {
                "models": {"my-model": {"enabled": True}}
            }
        }
        example_cfg = {
            "lm_translator": {
                "models": {"gemini-2.5-flash": {"enabled": True}}
            }
        }
        with _patch_config_files(tmp_path, user_cfg, example_cfg):
            cfg = load_config()
        assert "gemini-2.5-flash" not in cfg["lm_translator"]["models"]
        assert "my-model" in cfg["lm_translator"]["models"]


# =============================================================================
# Helper functions: get_default() and get_default_block()
# =============================================================================

class TestGetDefaultHelpers:
    """get_default() 與 get_default_block() 的正確性。"""

    def test_get_default_scalar(self):
        """get_default('path.to.key') 回傳 DEFAULT_CONFIG 中該路徑的值。"""
        assert get_default("translator.parallel_execution_workers") == 4
        assert get_default("lang_merger.pending_folder_name") == "待翻譯"
        assert get_default("lm_translator.temperature") == 0.3

    def test_get_default_returns_none_for_missing_path(self):
        """get_default() 對不存在的路徑回傳 None。"""
        assert get_default("nonexistent.key") is None

    def test_get_default_with_fallback(self):
        """get_default() 可帶第三參數作為 fallback。"""
        assert get_default("nonexistent.key", "FALLBACK") == "FALLBACK"
        assert get_default("translator.parallel_execution_workers", 999) == 4

    def test_get_default_block_returns_dict_copy(self):
        """get_default_block('name') 回傳該區塊的深拷貝。"""
        block = get_default_block("lang_merger")
        assert isinstance(block, dict)
        assert block["pending_folder_name"] == "待翻譯"
        block["pending_folder_name"] = "MODIFIED"
        assert get_default_block("lang_merger")["pending_folder_name"] == "待翻譯"

    def test_get_default_block_missing_returns_empty_dict(self):
        """get_default_block() 對不存在的區塊回傳空 dict。"""
        assert get_default_block("nonexistent_block") == {}

    def test_get_default_nested_path(self):
        """get_default() 支援多層巢狀路徑。"""
        assert get_default("extractor.output_folder_names.lang_extract") == "_提取lang_輸出"
        assert get_default("lm_translator.patchouli.dir_names") == ["patchouli_books", "book", "manual", "guidebook"]
