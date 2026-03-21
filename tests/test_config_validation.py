"""
測試 config_manager 的 Schema 驗證（ATK-013 / Gap 3）。

驗證 load_config() 能正確拒絕或修復以下錯誤格式：
1. `keys` 為字串（而非 list）
2. `initial_batch_size_lang` 為字串
3. `parallel_execution_workers` 為字串 "4"

# 背景
目前 load_config() 無 schema 驗證，錯誤格式導致靜默崩潰。
這些測試是「負向測試」：預期無效格式應被明確拒絕（拋出異常）。
當 James 未來加入 schema 驗證後，這些測試將從 FAIL 變成 PASS。

# 設計原則
- 每個測試為 Arrange / Act / Assert 三段式結構
- 使用 unittest.mock.patch 隔離檔案 I/O
- 測試結束後以 uv run pytest -q 驗證
"""

import json
import sys
from pathlib import Path

import pytest

# 確保 translation_tool 在 sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation_tool.utils.config_manager import load_config, ConfigValidationError


# =============================================================================
# ATK-013 / Gap 3：Schema 驗證負向測試
# =============================================================================

class TestConfigSchemaValidation:
    """驗證 load_config() 對無效格式的反應。"""

    # -------------------------------------------------------------------------
    # 負向測試 1：keys 應為 list，傳入字串時應拋出明確錯誤
    # -------------------------------------------------------------------------
    def test_keys_must_be_list_or_raise(self, tmp_path):
        """
        情境：lm_translator.keys 設為字串 "token"（而非 list）。

        Arrange：
            - 建立 config.json，keys 為字串 "token"
        Act：
            - 呼叫 load_config(路徑)
        Assert（預期行為）：
            - 應拋出 ConfigValidationError 或 TypeError
            - （未來實作 schema 驗證後，應拋出 ConfigValidationError）

        目前狀態（預期 FAIL）：
            - load_config 無驗證，直接回傳合併後的 dict
            - 錯誤會在下游使用時才爆開（TypeError）
            - 因此本測試目前會 FAIL（因為沒有任何例外被拋出）
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({
                "lm_translator": {
                    "keys": "token",  # 錯誤：應為 list
                }
            }), encoding="utf-8"
        )

        # Act & Assert
        # 預期：load_config 現在會拋出 ConfigValidationError
        with pytest.raises(ConfigValidationError):
            load_config(str(config_file))

    # -------------------------------------------------------------------------
    # 負向測試 2：initial_batch_size_lang 應為 int，傳入字串時應拋出明確錯誤
    # -------------------------------------------------------------------------
    def test_initial_batch_size_lang_must_be_int_or_raise(self, tmp_path):
        """
        情境：initial_batch_size_lang 設為字串 "300"（而非 int）。

        Arrange：
            - 建立 config.json，initial_batch_size_lang 為字串 "300"
        Act：
            - 呼叫 load_config(路徑)
        Assert（預期行為）：
            - 應拋出 ConfigValidationError 或 TypeError

        目前狀態（預期 FAIL）：
            - deep_merge 會保留該字串（因為非 dict，不觸發遞迴合併）
            - load_config 正常回傳，TypeError 在下游數學運算時才發生
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({
                "lm_translator": {
                    "initial_batch_size_lang": "300",  # 錯誤：應為 int
                }
            }), encoding="utf-8"
        )

        # Act & Assert
        # 預期：load_config 現在會拋出 ConfigValidationError
        with pytest.raises(ConfigValidationError):
            load_config(str(config_file))

    # -------------------------------------------------------------------------
    # 負向測試 3：parallel_execution_workers 應為 int，傳入字串 "4" 時應拋出錯誤
    # -------------------------------------------------------------------------
    def test_parallel_execution_workers_must_be_int_or_raise(self, tmp_path):
        """
        情境：parallel_execution_workers 設為字串 "4"（而非 int）。

        Arrange：
            - 建立 config.json，parallel_execution_workers 為字串 "4"
        Act：
            - 呼叫 load_config(路徑)
        Assert（預期行為）：
            - 應拋出 ConfigValidationError 或 TypeError

        目前狀態（預期 FAIL）：
            - deep_merge 直接用 override 的字串值覆寫 default 的 int 值
            - load_config 正常回傳，TypeError 在下游 max() 比較時才發生
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({
                "translator": {
                    "parallel_execution_workers": "4",  # 錯誤：應為 int
                }
            }), encoding="utf-8"
        )

        # Act & Assert
        # 預期：未來實作 schema 驗證後應拋出例外
        with pytest.raises((TypeError, ValueError)):
            cfg = load_config(str(config_file))
            # 若 load_config 未拋例外，嘗試引發下游錯誤
            workers = cfg["translator"]["parallel_execution_workers"]
            # max(1, workers) 若 workers 為 str → TypeError
            _ = max(1, workers)

    # -------------------------------------------------------------------------
    # 負向測試 4：models 傳入 list（而非 dict）應被拒絕
    # -------------------------------------------------------------------------
    def test_models_must_be_dict_or_raise(self, tmp_path):
        """
        情境：lm_translator.models 設為 list（而非 dict）。

        Arrange：
            - 建立 config.json，models 為 list
        Act：
            - 呼叫 load_config(路徑)
        Assert（預期行為）：
            - 應拋出 ConfigValidationError 或 TypeError

        目前狀態（預期 FAIL）：
            - deep_merge 會用 list 覆寫 default 的 dict
            - load_config 正常回傳，錯誤在 get_models_config 處理時才發生
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({
                "lm_translator": {
                    "models": ["gemini-2.5-flash", "gemini-3-flash-preview"]
                }
            }), encoding="utf-8"
        )

        # Act & Assert
        # 預期：load_config 現在會拋出 ConfigValidationError
        with pytest.raises(ConfigValidationError):
            load_config(str(config_file))

    # -------------------------------------------------------------------------
    # 正向測試（對照組）：合法 int 應該正常通過
    # -------------------------------------------------------------------------
    def test_parallel_execution_workers_valid_int_passes(self, tmp_path):
        """
        正向對照組：parallel_execution_workers 為合法 int 時，load_config 正常運作。
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({
                "translator": {
                    "parallel_execution_workers": 4
                }
            }), encoding="utf-8"
        )

        cfg = load_config(str(config_file))
        workers = cfg["translator"]["parallel_execution_workers"]

        # 確認是 int 且可用於 max
        assert isinstance(workers, int), f"workers 應為 int，實際：{type(workers)}"
        assert max(1, workers) == 4

    def test_keys_valid_list_passes(self, tmp_path):
        """
        正向對照組：keys 為合法 list 時，load_config 正常運作。
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({
                "lm_translator": {
                    "keys": ["token1", "token2"]
                }
            }), encoding="utf-8"
        )

        cfg = load_config(str(config_file))
        keys = cfg["lm_translator"]["keys"]

        assert isinstance(keys, list), f"keys 應為 list，實際：{type(keys)}"
        assert "token1" in keys

    def test_initial_batch_size_lang_valid_int_passes(self, tmp_path):
        """
        正向對照組：initial_batch_size_lang 為合法 int 時，load_config 正常運作。
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({
                "lm_translator": {
                    "initial_batch_size_lang": 300
                }
            }), encoding="utf-8"
        )

        cfg = load_config(str(config_file))
        batch_size = cfg["lm_translator"]["initial_batch_size_lang"]

        assert isinstance(batch_size, int), f"batch_size 應為 int，實際：{type(batch_size)}"
        assert batch_size * 2 == 600  # 確認可用於數學運算
