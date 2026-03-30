"""translation_tool/core/jar_icon_extractor.py 模組。

用途：批次從 JAR 檔案提取 icon 圖示（每個 JAR 只開一次）。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

# 類型別名
Entry = object  # SimpleNamespace with modid, key, source_jar


def _compute_jar_hash(jar_path: str) -> str:
    """計算 JAR 檔案的 SHA-256 hash（前 16 字元）作為 cache key。
    
    Args:
        jar_path: JAR 檔案路徑
    Returns:
        hash 字串
    """
    h = hashlib.sha256()
    with open(jar_path, 'rb') as f:
        # 只 hash 前 64KB + 檔名（快速估算）
        h.update(f.read(65536))
    h.update(jar_path.encode('utf-8'))
    return h.hexdigest()[:16]


def _load_json_from_zip(zf: zipfile.ZipFile, path: str) -> Optional[dict]:
    """安全讀取 ZIP 內的 JSON 檔案。
    
    Args:
        zf: 開啟的 ZipFile 物件
        path: 檔案路徑
    Returns:
        解析後的 dict，失敗回傳 None
    """
    try:
        with zf.open(path) as f:
            return json.loads(f.read().decode('utf-8'))
    except Exception:
        return None


class ModelIndex:
    """JAR 內所有模型檔案的索引，包含已解析的 texture 路徑。
    
    用於快取 model JSON 解析結果，避免重複開啟同一個 JAR。
    """
    
    def __init__(self, jar_path: str, zf: zipfile.ZipFile):
        """建立 ModelIndex。
        
        Args:
            jar_path: JAR 檔案路徑（用於除錯）
            zf: 開啟的 ZipFile 物件
        """
        self.jar_path = jar_path
        self.all_files: Set[str] = set(zf.namelist())
        self.texture_index: Dict[str, str] = {}  # 'modid:path' -> ZIP path
        self.model_cache: Dict[str, Optional[str]] = {}  # model path -> resolved texture path
        self._build_texture_index(zf)
    
    def _build_texture_index(self, zf: zipfile.ZipFile) -> None:
        """建立紋理索引：從 ZIP 內所有 textures/*.png 建立查詢表。
        
        Args:
            zf: 開啟的 ZipFile 物件
        """
        for f in self.all_files:
            if '/textures/' in f and f.endswith('.png'):
                # 'assets/modid/textures/item/name.png' -> 'modid:item/name'
                rel = f.replace('assets/', '', 1).replace('.png', '')
                parts = rel.split('/')
                if len(parts) >= 3:
                    modid = parts[0]
                    tex_path = '/'.join(parts[2:])
                    key = f'{modid}:{tex_path}'
                    self.texture_index[key] = f
    
    def resolve_texture(self, model_path: str, zf: zipfile.ZipFile, depth: int = 0) -> Optional[str]:
        """遞迴解析模型的紋理路徑（跟隨 parent chain）。
        
        Args:
            model_path: 模型檔案路徑（如 'assets/modid/models/item/name.json'）
            zf: 開啟的 ZipFile 物件
            depth: 遞迴深度（避免無窮迴圈）
        Returns:
            紋理檔案在 ZIP 內的路徑，找不到回傳 None
        """
        if depth > 10:
            return None
        
        if model_path in self.model_cache:
            return self.model_cache[model_path]
        
        model = _load_json_from_zip(zf, model_path)
        if model is None:
            self.model_cache[model_path] = None
            return None
        
        # 尝试从 textures 字典中找到任何已解析的纹理
        if 'textures' in model:
            for tex_ref in model['textures'].values():
                if tex_ref in self.texture_index:
                    result = self.texture_index[tex_ref]
                    self.model_cache[model_path] = result
                    return result
        
        # 跟隨 parent chain
        if 'parent' in model:
            parent = model['parent']
            if ':' in parent:
                parent_ns, parent_path = parent.split(':', 1)
                parent_model_path = f'assets/{parent_ns}/models/{parent_path}.json'
                if parent_model_path in self.all_files:
                    result = self.resolve_texture(parent_model_path, zf, depth + 1)
                    self.model_cache[model_path] = result
                    return result
        
        self.model_cache[model_path] = None
        return None
    
    def find_model_path(self, modid: str, name: str, is_block: bool = False) -> Optional[str]:
        """查詢模型檔案路徑。
        
        Args:
            modid: Mod ID
            name: 模型名稱（lang key 的最後一段）
            is_block: 是否為方塊模型
        Returns:
            模型檔案路徑，找不到回傳 None
        """
        prefix = 'block' if is_block else 'item'
        candidates = [
            f'assets/{modid}/models/{prefix}/{name}.json',
            f'assets/{modid}/models/{name}.json',
        ]
        for cp in candidates:
            if cp in self.all_files:
                return cp
        return None


def _extract_mod_icon_from_model(
    modid: str,
    lang_key: str,
    model_index: ModelIndex,
    zf: zipfile.ZipFile,
) -> Optional[str]:
    """從模型解析單一 lang key 對應的 icon 紋理路徑。
    
    Args:
        modid: Mod ID
        lang_key: 語言 key（如 'item.modid.drill_blue'）
        model_index: 已建立的 ModelIndex
        zf: 開啟的 ZipFile 物件
    Returns:
        紋理檔案在 ZIP 內的路徑，找不到回傳 None
    """
    # 從 lang key 取出模型名稱
    # e.g., 'item.actuallyadditions.drill_blue' -> 'drill_blue'
    parts = lang_key.split('.')
    if len(parts) < 2:
        return None
    
    name = parts[-1]
    
    # 嘗試找 item 模型
    model_path = model_index.find_model_path(modid, name, is_block=False)
    if model_path:
        texture_path = model_index.resolve_texture(model_path, zf)
        if texture_path:
            return texture_path
    
    # 嘗試找 block 模型
    model_path = model_index.find_model_path(modid, name, is_block=True)
    if model_path:
        texture_path = model_index.resolve_texture(model_path, zf)
        if texture_path:
            return texture_path
    
    return None


def _batch_extract_jar_icons(
    jar_path: str,
    entries: List[object],
    icon_cache_root: Path,
) -> Dict[str, Optional[str]]:
    """批次處理單一 JAR 的所有 entries，建立 icon_path_map。
    
    核心原則：每個 JAR 只開一次 ZIP，所有計算在記憶體內完成。
    
    Args:
        jar_path: JAR 檔案路徑
        entries: 屬於此 JAR 的 entries 清單（SimpleNamespace with modid, key）
        icon_cache_root: icon 快取根目錄
    Returns:
        dict[key -> icon_path]，icon_path 為快取內的檔案路徑或 None
    """
    result: Dict[str, Optional[str]] = {}
    
    # 快速掃描：收集此 JAR 內所有需要的 modid
    needed_modids: Set[str] = set()
    for e in entries:
        if hasattr(e, 'modid'):
            needed_modids.add(e.modid)
    
    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            # 建立 model index（整個 JAR 只建立一次）
            model_index = ModelIndex(jar_path, zf)
            
            # 解析每個 entry 的 icon
            for e in entries:
                key = getattr(e, 'key', None)
                modid = getattr(e, 'modid', None)
                if not key or not modid:
                    result[key or str(e)] = None
                    continue
                
                texture_zip_path = _extract_mod_icon_from_model(modid, key, model_index, zf)
                
                if texture_zip_path:
                    # 讀取紋理資料，計算 hash 做 deduplication
                    try:
                        with zf.open(texture_zip_path) as tex_f:
                            tex_data = tex_f.read()
                        tex_hash = hashlib.sha256(tex_data).hexdigest()[:16]
                        
                        # 檔名：{modid}_{jar_hash}_{content_hash}.png
                        jar_hash = _compute_jar_hash(jar_path)
                        icon_filename = f'{modid}_{jar_hash}_{tex_hash}.png'
                        icon_path = icon_cache_root / icon_filename
                        
                        # 寫入（若不存在）
                        if not icon_path.exists():
                            icon_cache_root.mkdir(parents=True, exist_ok=True)
                            icon_path.write_bytes(tex_data)
                        
                        result[key] = str(icon_path)
                    except Exception as ex:
                        log.warning(f'無法讀取紋理 {texture_zip_path} from {jar_path}: {ex}')
                        result[key] = None
                else:
                    result[key] = None
    
    except Exception as ex:
        log.error(f'處理 JAR {jar_path} 時發生錯誤: {ex}')
        for e in entries:
            key = getattr(e, 'key', None)
            result[key or str(e)] = None
    
    return result


def batch_extract_icons(
    entries: List[object],
    icon_cache_root: Path,
) -> Dict[str, Optional[str]]:
    """批次從多個 JAR 提取 icons。
    
    會按 source_jar 分組，每個 JAR 只開一次。
    
    Args:
        entries: 所有 entries（SimpleNamespace with modid, key, source_jar）
        icon_cache_root: icon 快取根目錄
    Returns:
        dict[key -> icon_path]，icon_path 為快取內的檔案路徑或 None
    """
    from collections import defaultdict
    
    # 按 source_jar 分組
    jar_to_entries: Dict[str, List[object]] = defaultdict(list)
    for e in entries:
        source_jar = getattr(e, 'source_jar', None)
        if source_jar:
            jar_to_entries[source_jar].append(e)
    
    # 處理每個 JAR
    all_results: Dict[str, Optional[str]] = {}
    total_jars = len(jar_to_entries)
    
    log.info(f'開始批次 icon 提取：共 {total_jars} 個 JAR，{len(entries)} 個 entries')
    
    for idx, (jar_path, jar_entries) in enumerate(jar_to_entries.items()):
        if idx % 50 == 0:
            log.info(f'Icon 提取進度：{idx}/{total_jars} JARs')
        
        results = _batch_extract_jar_icons(jar_path, jar_entries, icon_cache_root)
        all_results.update(results)
    
    log.info(f'Icon 提取完成：{total_jars} 個 JARs')
    return all_results


def get_lang_key_tail(lang_key: str) -> Optional[str]:
    """從 lang key 取出最後一段（模型名稱）。
    
    Args:
        lang_key: 語言 key（如 'item.modid.drill_blue'）
    Returns:
        模型名稱（如 'drill_blue'），空或無效回傳 None
    """
    if not lang_key or not isinstance(lang_key, str):
        return None
    parts = lang_key.split('.')
    if not parts or not parts[-1]:
        return None
    return parts[-1]


__all__ = [
    'batch_extract_icons',
    '_batch_extract_jar_icons',
    'ModelIndex',
    '_compute_jar_hash',
    '_extract_mod_icon_from_model',
]
