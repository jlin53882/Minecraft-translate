"""
Schema contract tests — 驗證 DEFAULT_CONFIG 的結構與型別不會被意外破壞。

這些測試的目的是：當未來有人修改 DEFAULT_CONFIG 時，能第一時間發現型別錯誤或欄位被刪。

覆蓋：
1. 所有 top-level keys 存在
2. 每個欄位的型別符合預期（int / float / bool / str / list / dict）
3. 關鍵 list 欄位的最小數量（防止被意外清空）
4. 必備的 nested dict 結構完整
"""

import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation_tool.utils.config_manager import DEFAULT_CONFIG


class TestSchemaKeys完整性:
    """驗證 DEFAULT_CONFIG 包含所有預期的 top-level keys。"""

    def test_has_all_expected_top_level_keys(self):
        expected = {
            "logging",
            "translator",
            "ftb_translator",
            "species_cache",
            "lm_translator",
            "output_bundler",
            "lang_merger",
            "jar_extractor",
            "extractor",
        }
        actual = set(DEFAULT_CONFIG.keys())
        missing = expected - actual
        extra = actual - expected
        assert not missing, f"缺少 top-level keys: {missing}"
        assert not extra, f"多了 top-level keys（確認是否新加入，如果是死設定應移除）: {extra}"


class TestSchema型別合約:
    """驗證每個欄位的型別符合預期，防止 int 變 str、bool 變 list 等錯誤。"""

    def test_translator_fields_types(self):
        t = DEFAULT_CONFIG["translator"]
        assert isinstance(t["cache_directory"], str)
        assert isinstance(t["enable_cache_saving"], bool)
        assert isinstance(t["parallel_execution_workers"], int)
        assert isinstance(t["replace_rules_path"], str)
        assert isinstance(t["output_dir_name"], str)
        assert isinstance(t["custom_translator_folder"], str)

    def test_ftb_translator_fields_types(self):
        f = DEFAULT_CONFIG["ftb_translator"]
        assert isinstance(f["output_dir_name"], str)

    def test_species_cache_fields_types(self):
        s = DEFAULT_CONFIG["species_cache"]
        # 確認欄位存在且型別正確
        assert isinstance(s["cache_directory"], str)
        assert isinstance(s.get("cache_filename"), str)
        assert isinstance(s.get("wikipedia_language"), str)
        assert isinstance(s.get("wikipedia_rate_limit_delay"), (int, float))

    def test_lang_merger_fields_types(self):
        lm = DEFAULT_CONFIG["lang_merger"]
        assert isinstance(lm["pending_folder_name"], str)
        assert isinstance(lm["pending_organized_folder_name"], str)
        assert isinstance(lm["filtered_pending_min_count"], int)
        assert isinstance(lm["quarantine_folder_name"], str)
        assert isinstance(lm["process_zh_cn_files"], bool)
        assert isinstance(lm["skip_zh_cn_when_only_process_lang"], bool)
        assert isinstance(lm["patchouli_skip_en_us_when_zh_cn_exists"], bool)
        assert isinstance(lm["patchouli_effective_translation_threshold"], float)

    def test_extractor_fields_types(self):
        e = DEFAULT_CONFIG["extractor"]
        assert isinstance(e["target_language"], list)
        assert isinstance(e["skip_zh_cn_extract"], bool)
        folders = e["output_folder_names"]
        assert isinstance(folders, dict)
        for v in folders.values():
            assert isinstance(v, str)

    def test_jar_extractor_fields_types(self):
        j = DEFAULT_CONFIG["jar_extractor"]
        assert isinstance(j["lang_codes"], list)
        for code in j["lang_codes"]:
            assert isinstance(code, str)

    def test_output_bundler_fields_types(self):
        o = DEFAULT_CONFIG["output_bundler"]
        assert isinstance(o["output_zip_name"], str)

    def test_lm_translator_fields_types(self):
        lm = DEFAULT_CONFIG["lm_translator"]
        assert isinstance(lm["temperature"], float)
        assert isinstance(lm["initial_batch_size_patchouli"], int)
        assert isinstance(lm["initial_batch_size_lang"], int)
        assert isinstance(lm["initial_batch_size_ftb"], int)
        assert isinstance(lm["initial_batch_size_kubejs"], int)
        assert isinstance(lm["initial_batch_size_md"], int)
        assert isinstance(lm["min_batch_size"], int)
        assert isinstance(lm["batch_shrink_factor"], float)
        assert isinstance(lm["keys"], list)
        # rate_limit 是 nested dict
        assert isinstance(lm["rate_limit"], dict)
        assert isinstance(lm["rate_limit"]["timeout"], int)
        assert isinstance(lm["rate_limit"]["sleep_seconds_between_batches"], (int, float))

    def test_lm_translator_models_is_dict(self):
        """models 必須是 dict（key = model name），不是 list。"""
        lm = DEFAULT_CONFIG["lm_translator"]
        assert isinstance(lm["models"], dict), "lm_translator.models 應為 dict，不應變成 list"

    def test_lm_translator_patchouli_system_prompt_is_string(self):
        lm = DEFAULT_CONFIG["lm_translator"]
        assert isinstance(lm["patchouli_system_prompt"], str)
        assert len(lm["patchouli_system_prompt"]) > 50, "system_prompt 不應是空字串"

    def test_lm_translator_lang_system_prompt_is_string(self):
        lm = DEFAULT_CONFIG["lm_translator"]
        assert isinstance(lm["lang_system_prompt"], str)
        assert len(lm["lang_system_prompt"]) > 50, "system_prompt 不應是空字串"


class TestList欄位數量基線:
    """驗證關鍵 list 欄位的數量，防止被意外清空或縮減低於合理範圍。"""

    def test_skip_terms_minimum_count(self):
        skip_terms = DEFAULT_CONFIG["lm_translator"]["translator"]["skip_terms"]
        assert isinstance(skip_terms, list)
        assert len(skip_terms) >= 20, f"skip_terms 只有 {len(skip_terms)} 項（預期 ≥ 20），可能被意外刪減"

    def test_translatable_keywords_minimum_count(self):
        kw = DEFAULT_CONFIG["lm_translator"]["translator"]["translatable_keywords"]
        assert isinstance(kw, list)
        assert len(kw) >= 15, f"translatable_keywords 只有 {len(kw)} 項（預期 ≥ 15），可能被意外刪減"

    def test_jar_extractor_lang_codes_includes_essential(self):
        codes = DEFAULT_CONFIG["jar_extractor"]["lang_codes"]
        assert "en_us" in codes, "jar_extractor.lang_codes 應包含 en_us"
        assert "zh_tw" in codes, "jar_extractor.lang_codes 應包含 zh_tw"
        assert "zh_cn" in codes, "jar_extractor.lang_codes 應包含 zh_cn"

    def test_extractor_target_language_not_empty(self):
        tl = DEFAULT_CONFIG["extractor"]["target_language"]
        assert isinstance(tl, list)
        assert len(tl) >= 1, "target_language 不應為空"

    def test_extractor_output_folder_names_all_present(self):
        folders = DEFAULT_CONFIG["extractor"]["output_folder_names"]
        expected = {"lang_extract", "book_extract", "lang_preview", "book_preview", "dual_extract", "dual_preview"}
        assert set(folders.keys()) == expected, f"output_folder_names 缺少欄位或有多餘欄位：目前為 {set(folders.keys())}"


class TestConfigExampleJsonSchema完整性:
    """驗證真實的 config.example.json 與 DEFAULT_CONFIG 結構對齊。"""

    def test_example_json_has_matching_keys(self, tmp_path):
        """config.example.json 的 top-level keys 應與 DEFAULT_CONFIG 相同。"""
        import json
        import translation_tool.utils.config_manager as cm

        # 用真實的 EXAMPLE_PATH
        example_path = cm.EXAMPLE_PATH
        if not example_path.exists():
            pytest.skip("config.example.json 不存在於 repo（正常行為）")

        with example_path.open(encoding="utf-8") as f:
            example = json.load(f)

        default_keys = set(DEFAULT_CONFIG.keys())
        example_keys = set(example.keys())

        # example 多了 key 是正常的（新版本預設值會先加在 example）
        # 但 DEFAULT 有而 example 沒有 → 應該是 example 漏寫了
        missing_in_example = default_keys - example_keys
        assert not missing_in_example, (
            f"config.example.json 缺少 DEFAULT_CONFIG 中已存在的 keys：{missing_in_example}。"
            "這代表新欄位只加了 DEFAULT_CONFIG 但沒同步到 example，"
            "會導致新安裝的使用者取不到這些設定。請將這些欄位補到 config.example.json。"
        )

    def test_example_prompt_fields_not_empty(self, tmp_path):
        """config.example.json 的 system_prompt 不應為空字串。"""
        import json
        import translation_tool.utils.config_manager as cm

        example_path = cm.EXAMPLE_PATH
        if not example_path.exists():
            pytest.skip("config.example.json 不存在")

        with example_path.open(encoding="utf-8") as f:
            example = json.load(f)

        patchouli_prompt = example.get("lm_translator", {}).get("patchouli_system_prompt", "")
        lang_prompt = example.get("lm_translator", {}).get("lang_system_prompt", "")

        assert patchouli_prompt, "config.example.json 的 patchouli_system_prompt 不應為空"
        assert lang_prompt, "config.example.json 的 lang_system_prompt 不應為空"
        assert len(patchouli_prompt) > 50, "patchouli_system_prompt 長度異常（疑似被清空）"
        assert len(lang_prompt) > 50, "lang_system_prompt 長度異常（疑似被清空）"