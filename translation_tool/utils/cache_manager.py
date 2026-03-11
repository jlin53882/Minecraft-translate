# /minecraft_translator_flet/translation_tool/utils/cache_manager.py (正式版)

import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any
import threading 
import datetime 
from concurrent.futures import ThreadPoolExecutor 

# --- 修正後的 import 路徑 ---
import orjson as json
from .config_manager import load_config
from . import cache_shards

log = logging.getLogger(__name__)

# --- 全域變數 ---
#_CACHE_DIR_NAME = "快取資料" #預設的快取資料夾名稱（"快取資料"）。
_cache_lock = threading.Lock() # ✅ 建立全域鎖定物件

CACHE_TYPES = ["lang", "patchouli", "ftbquests", "kubejs","md"]

_is_dirty: dict[str, bool] = {k: False for k in CACHE_TYPES} # 標記各類型快取是否有變更
# 新增：追蹤本次 Session 新增的翻譯，用於寫入新分
_session_new_entries: dict[str, dict] = {k: {} for k in CACHE_TYPES}


_CACHE_DIR_NAME = "快取資料夾"

# =========================
# Cache 設定（方案 A：依用途分檔）
# =========================

# --- 執行時期變數 ---
 #核心記憶體資料庫。巢狀結構：{ "cache_type": { "key": {"src": "...", "dst": "..."} } }。
_translation_cache: dict[str, dict] = {} 

#紀錄各類型快取在硬碟中的實體路徑，方便後續讀寫。
_cache_file_path: dict[str, Path] = {}

#狀態標記。確保初始化邏輯（讀取檔案）在程式生命週期中只執行一次。
_initialized = False

# --- 快取分片設定 ---
# 每個分片的最大條目數量，超過後會產生新分片。
# 這個值可以根據需求調整，2500 是一個平衡效能與管理的合理值。  儲存翻譯條目數量的上限
ROLLING_SHARD_SIZE = 2500
ACTIVE_SHARD_FILE = ".active" # 用於標記目前活躍分片的檔案名稱

# --- 原子化讀取分片 ---
# 新增一個輔助函式，用於單個分片的高速載入
def _load_shard_file(f_path: Path) -> dict:
    try:
        # 使用 read_bytes 配合 orjson 是最快的組合
        data = json.loads(f_path.read_bytes())
        return data if isinstance(data, dict) else {}
    except Exception as e:
        # 這裡紀錄日誌但不會中斷其他執行緒
        return {}


def _get_cache_root() -> Path:
    translation_config = load_config().get("translator", {})
    project_root = Path(os.getcwd())
    cache_dir_name = translation_config.get("cache_directory", _CACHE_DIR_NAME)
    return project_root / cache_dir_name


def _load_cache_type(cache_type: str):
    translation_config = load_config().get("translator", {})
    cache_root = _get_cache_root()

    if cache_type not in _translation_cache:
        _translation_cache[cache_type] = {}

    type_dir = cache_root / cache_type
    type_dir.mkdir(parents=True, exist_ok=True)
    _cache_file_path[cache_type] = type_dir / f"{cache_type}_cache_main.json"

    json_files = sorted(type_dir.glob("*.json"))
    if not json_files:
        _translation_cache[cache_type] = {}
        return

    loaded_count = 0
    work_thread = translation_config.get("parallel_execution_workers", 4)
    with ThreadPoolExecutor(max_workers=work_thread) as executor:
        results = list(executor.map(_load_shard_file, json_files))

    temp_cache = {}
    for data in results:
        if data:
            temp_cache.update(data)
            loaded_count += len(data)

    _translation_cache[cache_type] = temp_cache
    log.info(f"🚀 高速載入完成：{cache_type} 共 {loaded_count} 條翻譯 (分片數: {len(json_files)})")


# --- 初始化快取系統 ---
def initialize_translation_cache():
    global _translation_cache, _cache_file_path, _initialized
    if _initialized:
        return

    try:
        for cache_type in CACHE_TYPES:
            _load_cache_type(cache_type)
        _initialized = True
    except Exception as e:
        log.error(f"快取系統初始化失敗: {e}", exc_info=True)


# --- 核心快取操作函式 ---
def reload_translation_cache():
    """
    強制重新讀取所有 cache shard。
    - 會清空記憶體 cache 並重跑 initialize_translation_cache()
    - 若同時有其他執行緒在讀取 cache，呼叫端需自行協調
    """
    global _translation_cache, _cache_file_path, _initialized
    with _cache_lock:
        _translation_cache = {}
        _cache_file_path = {}
        _initialized = False
        _session_new_entries.clear()
        for k in CACHE_TYPES:
            _session_new_entries[k] = {}
            _is_dirty[k] = False
    initialize_translation_cache()


def reload_translation_cache_type(cache_type: str):
    """只重新載入單一 cache_type。"""
    if cache_type not in CACHE_TYPES:
        return

    initialize_translation_cache()
    with _cache_lock:
        _translation_cache[cache_type] = {}
        _session_new_entries[cache_type] = {}
        _is_dirty[cache_type] = False

    _load_cache_type(cache_type)

def _write_json_atomic(path: Path, data: dict):
    """Compatibility wrapper for shard persistence writes.

    The wrapper intentionally forwards the underlying return value as-is.
    Today the callee returns ``None``; preserving transparent passthrough keeps
    this facade stable if a future success/failure return contract is added.
    """
    return cache_shards._write_json_atomic(path, data)


def _save_entries_to_active_shards(cache_type: str, entries: dict, force_new_shard: bool = False):
    """
    將 entries 依 active shard 續寫；達 2500 自動切下一片。
    force_new_shard=True 時先切到新片再開始寫（對應「新分片」）。
    """
    type_dir = _cache_file_path[cache_type].parent
    return cache_shards._save_entries_to_active_shards(
        type_dir=type_dir,
        cache_type=cache_type,
        entries=entries,
        rolling_shard_size=ROLLING_SHARD_SIZE,
        active_shard_file=ACTIVE_SHARD_FILE,
        force_new_shard=force_new_shard,
        logger=log,
    )


def save_translation_cache(cache_type: str, write_new_shard: bool = True):
    if not load_config().get("translator", {}).get("enable_cache_saving", True):
        return

    data_to_save = {}

    with _cache_lock:
        src = _session_new_entries.get(cache_type)
        if not src:
            return

        data_to_save = src.copy()
        src.clear()
        _is_dirty[cache_type] = False

    try:
        save_path = _cache_file_path.get(cache_type)
        if not save_path:
            return

        # 新分片：先切新片再寫；補滿舊檔：續寫目前 active shard
        _save_entries_to_active_shards(
            cache_type,
            data_to_save,
            force_new_shard=write_new_shard,
        )
    except Exception as e:
        log.error(f"❌ 儲存 {cache_type} 失敗: {e}", exc_info=True)


def _get_active_shard_path(cache_type: str) -> Path:
    type_dir = _cache_file_path[cache_type].parent
    return cache_shards._get_active_shard_path(
        type_dir=type_dir,
        cache_type=cache_type,
        active_shard_file=ACTIVE_SHARD_FILE,
    )



def _rotate_shard_if_needed(cache_type: str, data: dict):
    type_dir = _cache_file_path[cache_type].parent
    return cache_shards._rotate_shard_if_needed(
        type_dir=type_dir,
        cache_type=cache_type,
        data=data,
        rolling_shard_size=ROLLING_SHARD_SIZE,
        active_shard_file=ACTIVE_SHARD_FILE,
        logger=log,
    )

def _extract_path_from_composite_key(key: str, src: str = "") -> str:
    """從 `path|source_text` 這類 composite key 取回 path。"""
    if not isinstance(key, str) or not key:
        return ""
    if src and key.endswith(f"|{src}"):
        return key[: -(len(src) + 1)]
    if "|" in key:
        return key.split("|", 1)[0]
    return key


def _infer_search_path(cache_type: str, key: str, entry: Dict[str, Any] | None) -> str:
    if isinstance(entry, dict):
        explicit = str(entry.get("path") or "").strip()
        if explicit:
            return explicit

    src = str((entry or {}).get("src") or "") if isinstance(entry, dict) else ""

    if cache_type in {"patchouli", "ftbquests", "kubejs", "md"}:
        return _extract_path_from_composite_key(key, src)

    if cache_type == "lang":
        return str(key or "")

    return _extract_path_from_composite_key(key, src)


def _infer_search_mod(cache_type: str, key: str, path: str, entry: Dict[str, Any] | None) -> str:
    if isinstance(entry, dict):
        explicit = str(entry.get("mod") or "").strip()
        if explicit:
            return explicit

    norm_path = str(path or "").replace("\\", "/").strip("/")
    parts = [p for p in norm_path.split("/") if p]

    for anchor in ("assets", "data"):
        if anchor in parts:
            idx = parts.index(anchor)
            if idx + 1 < len(parts):
                return parts[idx + 1]

    if cache_type == "lang":
        dotted = [p for p in str(key or "").split(".") if p]
        if len(dotted) >= 2:
            return dotted[1]

    fallback = {
        "ftbquests": "ftbquests",
        "kubejs": "kubejs",
        "md": "md",
    }
    return fallback.get(cache_type, "")


def _build_search_metadata(cache_type: str, key: str, entry: Dict[str, Any] | None) -> Dict[str, str]:
    path = _infer_search_path(cache_type, key, entry)
    mod = _infer_search_mod(cache_type, key, path, entry)
    return {"mod": mod, "path": path}


def add_to_cache(
    cache_type: str,
    key: str,
    src: str,
    dst: str,
    *,
    mod: str | None = None,
    path: str | None = None,
):
    if not key or not dst: return
    with _cache_lock:
        cache = _translation_cache.setdefault(cache_type, {})
        if cache.get(key, {}).get("dst") != dst:
            entry = {"src": src, "dst": dst}
            if mod:
                entry["mod"] = mod
            if path:
                entry["path"] = path
            cache[key] = entry
            # ⭐ 同步紀錄到 Session 緩存，用於產生新分片
            _session_new_entries[cache_type][key] = entry
            # ✅ 標記標籤：告訴 save 函式「資料變了，等一下記得存檔」
            _is_dirty[cache_type] = True


def get_from_cache(cache_type: str, key: str) -> Optional[str]:
    """
    回傳已翻譯的 dst 字串
    - 命中：str
    - 未命中 / cache 未初始化：None

    ⚠️ 注意：
    - 此 API 僅回傳 dst，不回傳完整 cache entry
    - 若需要 status / metadata，請新增新的 get_cache_entry()
    """
    if not _initialized:
        return None

    cache = _translation_cache.get(cache_type)
    if not cache:
        return None

    entry = cache.get(key)
    return entry.get("dst") if isinstance(entry, dict) else None

def get_cache_size_old() -> int:
    """獲取目前所有快取的總數量。"""
    return sum(len(c) for c in _translation_cache.values())


# cache_manager.py

"""
快速對照表（記住這張就好）
情境	               用哪個
大量 item cache 分流	get_cache_dict_ref()
fast split	           fast_split_items_by_cache()
單筆 cache 查詢	        get_cache_entry()
Recorder / QC	       get_cache_entry()
Debug / Preview	        get_cache_entry()
安全 fallback	        get_cache_entry()
"""
def get_cache_entry(cache_type: str, key: str) -> Optional[Dict[str, Any]]:
    """
    回傳完整 entry: {"src": "...", "dst": "..."}，未命中回 None
    """
    if not _initialized:
        return None
    cache = _translation_cache.get(cache_type)
    if not cache:
        return None
    entry = cache.get(key)
    return entry if isinstance(entry, dict) else None


def get_cache_dict_ref(cache_type: str) -> Dict[str, Dict[str, Any]]:
    """
    回傳底層快取 dict 的 reference（供高速讀取）
    注意：請勿在外部直接修改內容（寫入請用 add_to_cache）
    """
    if not _initialized:
        return {}
    cache = _translation_cache.get(cache_type)
    return cache if isinstance(cache, dict) else {}


def get_session_new_count(cache_type: str) -> int:
    with _cache_lock:
        return len(_session_new_entries.get(cache_type, {}))


def get_active_shard_id(cache_type: str) -> str:
    try:
        type_dir = _cache_file_path.get(cache_type, Path(".")).parent
        active_file = type_dir / ACTIVE_SHARD_FILE
        if active_file.exists():
            return active_file.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def get_cache_overview() -> Dict[str, Any]:
    initialize_translation_cache()
    out_types: Dict[str, Any] = {}
    total_entries = 0
    dirty_type_count = 0

    with _cache_lock:
        for cache_type in CACHE_TYPES:
            entries = len(_translation_cache.get(cache_type, {}))
            is_dirty = bool(_is_dirty.get(cache_type, False))
            session_new = len(_session_new_entries.get(cache_type, {}))
            active_id = get_active_shard_id(cache_type)

            active_entries = 0
            try:
                active_path = _get_active_shard_path(cache_type)
                if active_path.exists():
                    active_data = json.loads(active_path.read_bytes())
                    if isinstance(active_data, dict):
                        active_entries = len(active_data)
            except Exception:
                active_entries = 0

            if is_dirty:
                dirty_type_count += 1
            total_entries += entries

            out_types[cache_type] = {
                "entries_count": entries,
                "session_new_count": session_new,
                "is_dirty": is_dirty,
                "active_shard_id": active_id,
                "active_shard_entries": active_entries,
                "shard_capacity": ROLLING_SHARD_SIZE,
            }

    try:
        translation_config = load_config().get("translator", {})
        cache_dir_name = translation_config.get("cache_directory", _CACHE_DIR_NAME)
        cache_root = str((Path(os.getcwd()) / cache_dir_name).resolve())
    except Exception:
        cache_root = ""

    return {
        "cache_root": cache_root,
        "total_entries": total_entries,
        "dirty_type_count": dirty_type_count,
        "types": out_types,
        "last_reload_at": datetime.datetime.now().strftime("%H:%M:%S"),
        "last_save_at": None,
    }


def force_rotate_shard(cache_type: str) -> bool:
    initialize_translation_cache()
    if cache_type not in CACHE_TYPES:
        return False
    try:
        with _cache_lock:
            type_dir = _cache_file_path[cache_type].parent
            active_file = type_dir / ACTIVE_SHARD_FILE
            if not active_file.exists():
                _ = _get_active_shard_path(cache_type)
            cur = int((active_file.read_text(encoding="utf-8") or "1").strip())
            active_file.write_text(f"{cur + 1:05d}", encoding="utf-8")
        return True
    except Exception:
        return False


# =========================
# 快取搜尋功能（A3 改進）
# =========================

# 全域搜尋引擎實例（延遲初始化）
_search_engine = None
_search_engine_lock = threading.Lock()


def get_search_engine():
    """取得全域搜尋引擎實例（單例模式）"""
    global _search_engine
    
    if _search_engine is None:
        with _search_engine_lock:
            if _search_engine is None:  # Double-check locking
                try:
                    # 延遲導入避免循環依賴
                    from .cache_search import CacheSearchEngine
                    
                    # 搜尋索引存放在快取根目錄
                    cache_root = _get_cache_root()
                    db_path = str(cache_root / "search_index.db")
                    
                    _search_engine = CacheSearchEngine(db_path)
                    log.info(f"✅ 快取搜尋引擎已初始化: {db_path}")
                except Exception as e:
                    log.error(f"❌ 搜尋引擎初始化失敗: {e}", exc_info=True)
                    _search_engine = None
    
    return _search_engine


def rebuild_search_index():
    """重建搜尋索引（從記憶體快取）
    
    注意：這會清空舊索引並重新建立，可能需要幾秒鐘時間
    """
    global _search_engine
    
    try:
        log.info("🔄 開始重建搜尋索引...")
        
        # 關閉舊的搜尋引擎（如果存在）
        if _search_engine is not None:
            try:
                _search_engine.close()
            except:
                pass
            _search_engine = None
        
        # 刪除舊的索引檔案（確保重建表格結構）
        try:
            cache_root = _get_cache_root()
            db_path = cache_root / "search_index.db"
            if db_path.exists():
                import os
                os.remove(str(db_path))
                log.info("  ✓ 已刪除舊索引檔案")
        except Exception as e:
            log.warning(f"刪除舊索引失敗: {e}")
        
        # 重新初始化搜尋引擎（會建立新表格）
        engine = get_search_engine()
        if engine is None:
            log.error("搜尋引擎初始化失敗")
            return
        
        # 從記憶體快取重建
        total_indexed = 0
        for cache_type in CACHE_TYPES:
            cache_dict = _translation_cache.get(cache_type, {})
            
            # 批次準備資料
            entries = [
                {
                    'key': key,  # cache key（重要！）
                    'src': entry.get('src', ''),
                    'dst': entry.get('dst', ''),
                    'type': cache_type,
                    **_build_search_metadata(cache_type, key, entry),
                }
                for key, entry in cache_dict.items()
                if isinstance(entry, dict)
            ]
            
            if entries:
                engine.index_batch(entries)
                total_indexed += len(entries)
                log.info(f"  ✓ {cache_type}: 已索引 {len(entries)} 條")
        
        log.info(f"✅ 搜尋索引重建完成，共索引 {total_indexed} 條翻譯")
    
    except Exception as e:
        log.error(f"❌ 重建搜尋索引失敗: {e}", exc_info=True)


def rebuild_search_index_for_type(cache_type: str):
    """重建單一 cache_type 的搜尋索引。"""
    if cache_type not in CACHE_TYPES:
        return

    engine = get_search_engine()
    if engine is None:
        log.warning("搜尋引擎未初始化，無法重建單一分類索引")
        return

    try:
        # 先刪掉該分類舊索引
        engine.clear_index_by_type(cache_type)

        # 再重建該分類
        cache_dict = _translation_cache.get(cache_type, {})
        entries = [
            {
                'key': key,
                'src': entry.get('src', ''),
                'dst': entry.get('dst', ''),
                'type': cache_type,
                **_build_search_metadata(cache_type, key, entry),
            }
            for key, entry in cache_dict.items()
            if isinstance(entry, dict)
        ]

        if entries:
            engine.index_batch(entries)
        log.info(f"✅ {cache_type} 索引重建完成（{len(entries)} 條）")
    except Exception as e:
        log.error(f"❌ {cache_type} 索引重建失敗: {e}", exc_info=True)


def search_cache(
    query: str,
    cache_type: str = None,
    limit: int = 50,
    use_fuzzy: bool = True
) -> list:
    """搜尋快取
    
    Args:
        query: 搜尋關鍵字（可以是原文或譯文）
        cache_type: 限定快取類型（lang/patchouli 等），None 表示搜尋全部
        limit: 回傳結果數量上限
        use_fuzzy: 是否使用模糊比對重新排序
    
    Returns:
        搜尋結果清單，每個結果包含：
        - src: 原文
        - dst: 譯文
        - type: 快取類型
        - score: 相關度分數（越高越相關）
    
    Example:
        results = search_cache("creeper", cache_type="lang", limit=20)
        for r in results:
            print(f"{r['src']} → {r['dst']} (score: {r.get('score', 0):.2f})")
    """
    engine = get_search_engine()
    if engine is None:
        log.warning("搜尋引擎未初始化")
        return []
    
    try:
        results = engine.search(query, limit=limit, cache_type=cache_type)
        
        # 如果啟用模糊比對，使用 FuzzyMatcher 重新評分
        if use_fuzzy and results:
            from .cache_search import FuzzyMatcher
            matcher = FuzzyMatcher()
            results = matcher.rank_results(query, results)
        
        return results
    
    except Exception as e:
        log.error(f"搜尋失敗: {e}", exc_info=True)
        return []


def find_similar_translations(
    text: str,
    cache_type: str = None,
    threshold: float = 0.6,
    limit: int = 20
) -> list:
    """找出相似的翻譯（基於模糊比對）
    
    Args:
        text: 要比對的文字
        cache_type: 限定快取類型
        threshold: 相似度門檻（0~1）
        limit: 結果數量上限
    
    Returns:
        相似翻譯清單，包含 'similarity' 欄位（0~1）
    """
    from .cache_search import FuzzyMatcher
    
    # 先用搜尋引擎找候選項
    candidates = search_cache(text, cache_type=cache_type, limit=limit * 2, use_fuzzy=False)
    
    if not candidates:
        return []
    
    # 模糊比對
    matcher = FuzzyMatcher()
    similar = matcher.find_similar(
        text,
        candidates,
        threshold=threshold,
        key_field='src'
    )
    
    return similar[:limit]


# --- 當模組被匯入時，自動執行初始化 ---
initialize_translation_cache()
log.info(
    "快取統計：" +
    ", ".join(f"{k}={len(v)}" for k, v in _translation_cache.items())
)

# 延遲初始化搜尋引擎（避免拖慢啟動速度）
# 搜尋引擎會在第一次呼叫 search_cache() 時自動建立
