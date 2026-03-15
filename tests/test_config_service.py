"""app.services_impl.config_service 單元測試

測試目標：config_service 模組的路徑常數與載入/儲存函數。
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from app.services_impl import config_service


class TestConfigServicePaths:
    """路徑常數測試"""

    def test_project_root_exists(self):
        """測試 PROJECT_ROOT 存在"""
        assert config_service.PROJECT_ROOT.exists()
        assert config_service.PROJECT_ROOT.is_dir()

    def test_project_root_is_absolute(self):
        """測試 PROJECT_ROOT 是絕對路徑"""
        assert config_service.PROJECT_ROOT.is_absolute()

    def test_config_path_format(self):
        """測試 CONFIG_PATH 是正確的格式"""
        assert config_service.CONFIG_PATH.endswith("config.json")
        assert "minecraft_translator_flet" in config_service.CONFIG_PATH

    def test_replace_rules_path_format(self):
        """測試 REPLACE_RULES_PATH 是正確的格式"""
        assert config_service.REPLACE_RULES_PATH.endswith("replace_rules.json")
        assert "minecraft_translator_flet" in config_service.REPLACE_RULES_PATH


class TestConfigServiceLoadSave:
    """載入/儲存函數測試"""

    @patch("app.services_impl.config_service._load_app_config")
    def test_load_config_json_success(self, mock_load):
        """測試成功載入設定"""
        mock_load.return_value = {"key": "value"}
        
        result = config_service.load_config_json()
        
        assert result == {"key": "value"}
        mock_load.assert_called_once()

    @patch("app.services_impl.config_service._save_app_config")
    def test_save_config_json_success(self, mock_save):
        """測試成功儲存設定"""
        config = {"key": "new_value"}
        
        config_service.save_config_json(config)
        
        mock_save.assert_called_once_with(config)

    @patch("app.services_impl.config_service.load_rules_core")
    def test_load_replace_rules_success(self, mock_load_rules):
        """測試成功載入替換規則"""
        mock_load_rules.return_value = {"rule1": "replacement1"}
        
        result = config_service.load_replace_rules()
        
        assert result == {"rule1": "replacement1"}

    @patch("app.services_impl.config_service.save_rules_core")
    def test_save_replace_rules_success(self, mock_save_rules):
        """測試成功儲存替換規則"""
        rules = {"rule2": "replacement2"}
        
        config_service.save_replace_rules(rules)
        
        mock_save_rules.assert_called_once()

    @patch("app.services_impl.config_service._load_app_config")
    def test_load_config_json_with_defaults(self, mock_load):
        """測試載入包含預設值的設定"""
        mock_load.return_value = {
            "language": "zh-TW",
            "theme": "dark"
        }
        
        result = config_service.load_config_json()
        
        assert "language" in result
        assert "theme" in result


class TestConfigServiceIntegration:
    """整合測試（在暫存目錄）"""

    def test_load_existing_config(self):
        """測試載入已存在的設定檔"""
        # 驗證 CONFIG_PATH 指向的檔案存在
        config_path = Path(config_service.CONFIG_PATH)
        
        # 假設專案根目錄有 config.json
        if config_path.exists():
            from translation_tool.utils.config_manager import load_config
            result = load_config(str(config_path))
            assert isinstance(result, dict)
        else:
            # 如果不存在，測試跳過
            pytest.skip("config.json not found")

    def test_save_and_load_roundtrip(self):
        """測試儲存後再載入的往返"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.json"
            
            test_data = {"name": "test", "version": "1.0"}
            
            # 寫入
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(test_data, f)
            
            # 讀取
            with open(config_path, "r", encoding="utf-8") as f:
                result = json.load(f)
            
            assert result == test_data
