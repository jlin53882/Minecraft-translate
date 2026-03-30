"""測試 jar_icon_extractor.py - JAR icon 批次提取。

用途：測試批次從 JAR 檔案提取 icon 的功能。
"""

import hashlib
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
import tempfile

import pytest

from translation_tool.core.jar_icon_extractor import (
    batch_extract_icons,
    _batch_extract_jar_icons,
    ModelIndex,
    _compute_jar_hash,
    _extract_mod_icon_from_model,
    get_lang_key_tail,
)


# 測試用 JAR 檔案
TEST_JAR = r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3\mods\actuallyadditions-1.3.19+mc1.21.1.jar'


class TestComputeJarHash:
    """測試 _compute_jar_hash 函數"""
    
    def test_returns_16_char_hash(self):
        """測試回傳 16 字元 hash"""
        h = _compute_jar_hash(TEST_JAR)
        assert len(h) == 16
        assert h.isalnum()
    
    def test_same_file_same_hash(self):
        """測試相同檔案產生相同 hash"""
        h1 = _compute_jar_hash(TEST_JAR)
        h2 = _compute_jar_hash(TEST_JAR)
        assert h1 == h2


class TestGetLangKeyTail:
    """測試 get_lang_key_tail 函數"""
    
    def test_item_key(self):
        """測試 item key"""
        assert get_lang_key_tail('item.modid.drill_blue') == 'drill_blue'
    
    def test_block_key(self):
        """測試 block key"""
        assert get_lang_key_tail('block.modid.cobblestone') == 'cobblestone'
    
    def test_simple_key(self):
        """測試簡單 key"""
        assert get_lang_key_tail('simple') == 'simple'
    
    def test_empty_key(self):
        """測試空 key"""
        assert get_lang_key_tail('') is None


class TestModelIndex:
    """測試 ModelIndex 類別"""
    
    @pytest.fixture
    def model_index(self):
        """建立 ModelIndex fixture"""
        with zipfile.ZipFile(TEST_JAR, 'r') as zf:
            return ModelIndex(TEST_JAR, zf)
    
    def test_texture_index_built(self, model_index):
        """測試紋理索引已建立"""
        assert len(model_index.texture_index) > 0
        # 確認包含 actuallyadditions 的紋理
        assert any('actuallyadditions' in k for k in model_index.texture_index.keys())
    
    def test_find_item_model(self, model_index):
        """測試查詢 item 模型"""
        path = model_index.find_model_path('actuallyadditions', 'drill_blue')
        assert path == 'assets/actuallyadditions/models/item/drill_blue.json'
    
    def test_find_block_model(self, model_index):
        """測試查詢 block 模型"""
        path = model_index.find_model_path('actuallyadditions', 'black_quartz_block')
        assert path is not None
        assert 'block' in path
    
    def test_resolve_texture(self, model_index):
        """測試解析紋理"""
        with zipfile.ZipFile(TEST_JAR, 'r') as zf:
            model_path = 'assets/actuallyadditions/models/item/drill_blue.json'
            texture_path = model_index.resolve_texture(model_path, zf)
            assert texture_path is not None
            assert texture_path.endswith('.png')
            assert 'drill_blue' in texture_path


class TestExtractModIconFromModel:
    """測試 _extract_mod_icon_from_model 函數"""
    
    @pytest.fixture
    def model_index(self):
        """建立 ModelIndex fixture"""
        with zipfile.ZipFile(TEST_JAR, 'r') as zf:
            return ModelIndex(TEST_JAR, zf)
    
    def test_extract_item_icon(self, model_index):
        """測試提取 item icon"""
        with zipfile.ZipFile(TEST_JAR, 'r') as zf:
            result = _extract_mod_icon_from_model(
                'actuallyadditions',
                'item.actuallyadditions.drill_blue',
                model_index,
                zf
            )
            assert result is not None
            assert result.endswith('.png')
    
    def test_extract_block_icon(self, model_index):
        """測試提取 block icon"""
        with zipfile.ZipFile(TEST_JAR, 'r') as zf:
            result = _extract_mod_icon_from_model(
                'actuallyadditions',
                'block.actuallyadditions.black_quartz_block',
                model_index,
                zf
            )
            assert result is not None
    
    def test_nonexistent_returns_none(self, model_index):
        """測試不存在的 key 回傳 None"""
        with zipfile.ZipFile(TEST_JAR, 'r') as zf:
            result = _extract_mod_icon_from_model(
                'actuallyadditions',
                'item.actuallyadditions.nonexistent_item',
                model_index,
                zf
            )
            assert result is None


class TestBatchExtractJarIcons:
    """測試 _batch_extract_jar_icons 函數"""
    
    def test_batch_extract_single_jar(self):
        """測試批次提取單一 JAR"""
        with tempfile.TemporaryDirectory() as tmpdir:
            icon_cache = Path(tmpdir) / 'icon_cache'
            
            # 建立測試 entries
            entries = [
                SimpleNamespace(modid='actuallyadditions', key='item.actuallyadditions.drill_blue'),
                SimpleNamespace(modid='actuallyadditions', key='item.actuallyadditions.black_quartz'),
                SimpleNamespace(modid='actuallyadditions', key='item.actuallyadditions.crafter_on_a_stick'),
            ]
            
            results = _batch_extract_jar_icons(TEST_JAR, entries, icon_cache)
            
            assert len(results) == 3
            # 應該至少有 1 個成功
            success_count = sum(1 for v in results.values() if v is not None)
            assert success_count >= 1
    
    def test_deduplication(self):
        """測試相同 icon 內容的 deduplication"""
        with tempfile.TemporaryDirectory() as tmpdir:
            icon_cache = Path(tmpdir) / 'icon_cache'
            
            # 兩個 entry 應該解析到相同 icon（同一個 key）
            entries = [
                SimpleNamespace(modid='actuallyadditions', key='item.actuallyadditions.drill_blue'),
                SimpleNamespace(modid='actuallyadditions', key='item.actuallyadditions.drill_blue'),  # 重複 key
            ]
            
            results = _batch_extract_jar_icons(TEST_JAR, entries, icon_cache)
            
            # 由於 key 相同，dict 只有一個 entry
            # 但驗證：產生的 icon 檔案存在（表示有成功處理）
            values = list(results.values())
            assert len(values) == 1  # key 相同所以只有一個 entry
            assert values[0] is not None  # 應該成功解析到 icon
            
            # 驗證 icon 檔案只有一份（deduplication）
            icon_files = list(icon_cache.glob('*.png'))
            assert len(icon_files) == 1  # 相同內容應該只有一個檔案


class TestBatchExtractIcons:
    """測試 batch_extract_icons 函數（多 JAR 版本）"""
    
    def test_multiple_jars(self):
        """測試多個 JAR 的批次提取"""
        # 取得測試資料夾中的兩個不同 JAR
        mods_dir = Path(r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3\mods')
        jar_files = list(mods_dir.glob('*.jar'))[:2]
        
        if len(jar_files) < 2:
            pytest.skip('需要至少 2 個 JAR 檔案進行測試')
        
        with tempfile.TemporaryDirectory() as tmpdir:
            icon_cache = Path(tmpdir) / 'icon_cache'
            
            # 建立跨 JAR 的 entries
            jar1_name = str(jar_files[0])
            jar2_name = str(jar_files[1])
            
            # 從 JAR 檔名推斷 modid
            def get_modid(jar_path):
                name = os.path.basename(jar_path)
                # 去除版本號碼
                import re
                cleaned = re.sub(r'[-_](neoforge|forge|fabric|quilt|build|release|alpha|beta|\d+(?:\.\d+)*)[-_]?', '-', name, flags=re.IGNORECASE)
                cleaned = cleaned.rstrip('-_').rsplit('-', 1)[0]
                return cleaned.lower()
            
            entries = [
                SimpleNamespace(modid=get_modid(jar1_name), key=f'item.{get_modid(jar1_name)}.test', source_jar=jar1_name),
                SimpleNamespace(modid=get_modid(jar2_name), key=f'item.{get_modid(jar2_name)}.test', source_jar=jar2_name),
            ]
            
            results = batch_extract_icons(entries, icon_cache)
            
            assert len(results) == 2
