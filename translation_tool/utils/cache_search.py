"""快取搜尋系統

功能：
1. 全文搜尋（SQLite FTS5）
2. 模糊比對（Fuzzy Matching）
3. 相似詞推薦

注意：
- 不實作快取過期機制（所有快取永久保留）
- 搜尋結果按相關度排序

使用方式：
    from translation_tool.utils.cache_search import CacheSearchEngine

    engine = CacheSearchEngine(db_path="cache/search_index.db")
    engine.index_cache_entry({'src': 'Hello', 'dst': '你好', 'mod': 'test', 'path': 'test.json'})
    results = engine.search("你好", limit=50)
"""

import sqlite3
import threading
import time
from pathlib import Path
from difflib import SequenceMatcher
from typing import List, Dict, Optional, Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import cache_store
from .log_unit import log_info, log_warning, log_debug

# =============================================================================
# 全文搜尋引擎
# =============================================================================

class CacheSearchEngine:
    """快取全文搜尋引擎（使用 SQLite FTS5）"""

    def __init__(self, db_path: str | None = None):
        """初始化搜尋引擎

        Args:
            db_path: SQLite 資料庫路徑（預設：cache/search_index.db）
        """
        if db_path is None:
            cache_dir = Path("cache")
            cache_dir.mkdir(exist_ok=True)
            db_path = str(cache_dir / "search_index.db")

        self.db_path = db_path
        # SQLite 預設限制「同一個 connection 只能在建立它的 thread 使用」。
        # 本專案（Flet UI）常用 threading 跑背景任務，因此這裡允許跨 thread 使用。
        # 但為了安全，所有 DB 操作都必須搭配 self._lock 序列化。
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # 讓結果可以用欄位名稱存取
        self._lock = threading.RLock()
        
        # SQLite 效能優化（大幅提升大量寫入速度）
        with self._lock:
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = NORMAL")  # ATK-011: OFF 會導致 daemon crash 後 WAL recovery corruption
            self.conn.execute("PRAGMA cache_size = 10000")
            self.conn.execute("PRAGMA temp_store = MEMORY")
        
        self._init_fts_table()

    def _init_fts_table(self):
        """建立 FTS5 虛擬表（全文搜尋索引）+ basic 表格（雙保險）"""
        try:
            # 檢查表格是否存在且結構正確
            with self._lock:
                cursor = self.conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='cache_fts'"
                )
                existing = cursor.fetchone()

            # 如果表格存在但結構不對（沒有 cache_key），刪除重建
            with self._lock:
                if existing and "cache_key" not in existing[0]:
                    log_info("偵測到舊索引表格，正在刪除重建...")
                    self.conn.execute("DROP TABLE IF EXISTS cache_fts")
                    self.conn.commit()

                self.conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS cache_fts USING fts5(
                        cache_key,
                        source_text,
                        translated_text,
                        mod_name,
                        file_path,
                        cache_type,
                        tokenize = 'unicode61 remove_diacritics 2'
                    )
                """)
                self.conn.commit()
        except sqlite3.OperationalError as e:
            # FTS5 可能不支援（SQLite 版本過舊）
            log_warning(f"FTS5 初始化失敗，將使用基本搜尋: {e}")

        # 不論 FTS5 成功與否，都建立 basic 表格做為 fallback（雙保險）
        self._init_basic_table()

    def _init_basic_table(self):
        """建立基本表格（當 FTS5 不可用時）"""
        # 檢查表格是否存在且結構正確
        with self._lock:
            cursor = self.conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='cache_basic'"
            )
            existing = cursor.fetchone()

        # 如果表格存在但結構不對（沒有 cache_key），刪除重建
        with self._lock:
            if existing and "cache_key" not in existing[0]:
                log_info("偵測到舊基本表格，正在刪除重建...")
                self.conn.execute("DROP TABLE IF EXISTS cache_basic")
                self.conn.commit()

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_basic (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT,
                    source_text TEXT,
                    translated_text TEXT,
                    mod_name TEXT,
                    file_path TEXT,
                    cache_type TEXT
                )
            """)

        # 建立索引加速搜尋
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_source_text
            ON cache_basic(source_text)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_translated_text
            ON cache_basic(translated_text)
        """)
        self.conn.commit()

    def index_cache_entry(self, entry: dict):
        """將快取條目加入索引

        Args:
            entry: 快取條目字典，包含：
                - key: cache key（必要！）
                - src: 原文
                - dst: 譯文
                - mod: 模組名稱（可選）
                - path: 檔案路徑（可選）
                - type: 快取類型（lang/patchouli/ftbquests 等，可選）
        """
        key = entry.get("key", "")
        src = entry.get("src", "")
        dst = entry.get("dst", "")
        mod = entry.get("mod", "")
        path = entry.get("path", "")
        cache_type = entry.get("type", "lang")

        with self._lock:
            try:
                # 嘗試插入 FTS5 表
                self.conn.execute(
                    """
                    INSERT INTO cache_fts (cache_key, source_text, translated_text, mod_name, file_path, cache_type)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (key, src, dst, mod, path, cache_type),
                )
            except sqlite3.OperationalError:
                # FTS5 不可用，使用基本表
                self.conn.execute(
                    """
                    INSERT INTO cache_basic (cache_key, source_text, translated_text, mod_name, file_path, cache_type)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (key, src, dst, mod, path, cache_type),
                )

            self.conn.commit()

    def index_batch(self, entries: List[dict], batch_size: int = 20000):
        """批次加入索引（效能更好）

        Args:
            entries: 快取條目清單
            batch_size: 每批處理筆數（避免記憶體佔用過大）
        """
        if not entries:
            return
        
        t0 = time.time()
            
        data = [
            (
                e.get("key", ""),
                e.get("src", ""),
                e.get("dst", ""),
                e.get("mod", ""),
                e.get("path", ""),
                e.get("type", "lang"),
            )
            for e in entries
        ]
        
        prepare_time = time.time() - t0

        with self._lock:
            try:
                try:
                    self.conn.execute("PRAGMA recursive_triggers = OFF")
                except Exception:
                    pass
                
                write_start = time.time()
                for i in range(0, len(data), batch_size):
                    batch = data[i:i + batch_size]
                    self.conn.executemany(
                        """
                        INSERT INTO cache_fts (cache_key, source_text, translated_text, mod_name, file_path, cache_type)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        batch,
                    )
            except sqlite3.OperationalError:
                write_start = time.time()
                for i in range(0, len(data), batch_size):
                    batch = data[i:i + batch_size]
                    self.conn.executemany(
                        """
                        INSERT INTO cache_basic (cache_key, source_text, translated_text, mod_name, file_path, cache_type)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        batch,
                    )

            write_time = time.time() - write_start
            self.conn.commit()
        
        total_time = time.time() - t0
        log_debug(f"index_batch: {len(entries)} entries, prepare={prepare_time:.2f}s, write={write_time:.2f}s, total={total_time:.2f}s")

    def search(self, query: str, limit: int = 50, cache_type: str | None = None) -> List[Dict]:
        """搜尋快取（支援中英文、模糊比對）

        Args:
            query: 搜尋關鍵字
            limit: 回傳結果數量上限
            cache_type: 快取類型過濾（lang/patchouli 等，可選）

        Returns:
            搜尋結果清單，每個結果包含：
            - src: 原文
            - dst: 譯文
            - mod: 模組名稱
            - path: 檔案路徑
            - type: 快取類型
            - score: 相關度分數（FTS5 提供，基本搜尋無此欄位）
        """
        if not query or not query.strip():
            return []

        query = query.strip()

        # 嘗試 FTS5 搜尋
        try:
            sql = """
                SELECT cache_key, source_text, translated_text, mod_name, file_path, cache_type, rank
                FROM cache_fts
                WHERE cache_fts MATCH ?
            """
            params: list[str | int] = [query]

            if cache_type:
                sql += " AND cache_type = ?"
                params.append(cache_type)

            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)

            with self._lock:
                cursor = self.conn.execute(sql, params)
                rows = cursor.fetchall()

            return [
                {
                    "key": row["cache_key"],  # cache key
                    "src": row["source_text"],
                    "dst": row["translated_text"],
                    "mod": row["mod_name"],
                    "path": row["file_path"],
                    "type": row["cache_type"],
                    "score": -row["rank"],  # rank 是負數，轉成正數便於理解
                }
                for row in rows
            ]

        except sqlite3.OperationalError:
            # FTS5 不可用，使用基本 LIKE 搜尋
            return self._basic_search(query, limit, cache_type)

    def _basic_search(
        self, query: str, limit: int, cache_type: str | None = None
    ) -> List[Dict]:
        """基本搜尋（當 FTS5 不可用時）"""
        sql = """
            SELECT cache_key, source_text, translated_text, mod_name, file_path, cache_type
            FROM cache_basic
            WHERE source_text LIKE ? OR translated_text LIKE ?
        """
        params: list[str | int] = [f"%{query}%", f"%{query}%"]

        if cache_type:
            sql += " AND cache_type = ?"
            params.append(cache_type)

        sql += " LIMIT ?"
        params.append(limit)

        with self._lock:
            cursor = self.conn.execute(sql, params)
            rows = cursor.fetchall()

        return [
            {
                "key": row["cache_key"],  # cache key
                "src": row["source_text"],
                "dst": row["translated_text"],
                "mod": row["mod_name"],
                "path": row["file_path"],
                "type": row["cache_type"],
            }
            for row in rows
        ]

    def clear_index(self):
        """清空索引（慎用！）"""
        with self._lock:
            try:
                self.conn.execute("DELETE FROM cache_fts")
            except sqlite3.OperationalError:
                self.conn.execute("DELETE FROM cache_basic")
            self.conn.commit()

    def clear_index_by_type(self, cache_type: str):
        """只清空指定類型的索引資料。"""
        with self._lock:
            try:
                self.conn.execute(
                    "DELETE FROM cache_fts WHERE cache_type = ?", (cache_type,)
                )
            except sqlite3.OperationalError:
                self.conn.execute(
                    "DELETE FROM cache_basic WHERE cache_type = ?", (cache_type,)
                )
            self.conn.commit()

    def close(self):
        """關閉資料庫連線"""
        with self._lock:
            self.conn.close()

    def __enter__(self):
        """進入上下文管理，回傳自身。"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """離開上下文時關閉搜尋引擎。"""
        self.close()

# =============================================================================
# 模糊比對器
# =============================================================================

class FuzzyMatcher:
    """模糊比對器（相似度計算）"""

    @staticmethod
    def similarity(a: str, b: str) -> float:
        """計算兩個字串的相似度（0~1）

        Args:
            a: 字串 A
            b: 字串 B

        Returns:
            相似度分數（0 = 完全不同，1 = 完全相同）
        """
        if not a or not b:
            return 0.0

        # 使用 SequenceMatcher（基於最長公共子序列）
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def find_similar(
        self,
        query: str,
        candidates: List[dict],
        threshold: float = 0.6,
        key_field: str = "src",
    ) -> List[dict]:
        """找出相似的候選項

        Args:
            query: 查詢字串
            candidates: 候選項清單（字典清單）
            threshold: 相似度門檻（0~1）
            key_field: 要比對的欄位名稱（預設 'src'）

        Returns:
            相似候選項清單，每個項目新增 'similarity' 欄位
        """
        results = []

        for candidate in candidates:
            text = candidate.get(key_field, "")
            score = self.similarity(query, text)

            if score >= threshold:
                results.append({**candidate, "similarity": score})

        # 按相似度排序（高到低）
        return sorted(results, key=lambda x: x["similarity"], reverse=True)

    def rank_results(
        self,
        query: str,
        results: List[dict],
        src_weight: float = 0.6,
        dst_weight: float = 0.4,
    ) -> List[dict]:
        """對搜尋結果重新評分（同時考慮原文與譯文的相似度）

        Args:
            query: 查詢字串
            results: 搜尋結果
            src_weight: 原文相似度權重
            dst_weight: 譯文相似度權重

        Returns:
            重新評分後的結果（新增 'combined_score' 欄位）
        """
        scored = []

        for result in results:
            src_sim = self.similarity(query, result.get("src", ""))
            dst_sim = self.similarity(query, result.get("dst", ""))

            # 加權平均
            combined_score = src_sim * src_weight + dst_sim * dst_weight

            scored.append({**result, "combined_score": combined_score})

        # 按綜合分數排序
        return sorted(scored, key=lambda x: x["combined_score"], reverse=True)

# =============================================================================
# 便利函式
# =============================================================================

def search_cache(
    query: str,
    db_path: str = None,
    limit: int = 50,
    fuzzy: bool = True,
    threshold: float = 0.6,
) -> List[Dict]:
    """快取搜尋的便利函式"""
    with CacheSearchEngine(db_path) as engine:
        results = engine.search(query, limit)

        if fuzzy and results:
            matcher = FuzzyMatcher()
            results = matcher.rank_results(query, results)

        return results

# =============================================================================
# 搜尋協調輔助函式（PR12）
# =============================================================================

def _extract_path_from_composite_key(key: str, src: str = "") -> str:
    """從複合 key 拆出路徑段。

    目前 key 可能長成 `path|src`；若提供 src，會優先以尾段對齊拆分，
    避免 src 本身含有 `|` 時誤切。
    """
    if not isinstance(key, str) or not key:
        return ""
    if src and key.endswith(f"|{src}"):
        return key[: -(len(src) + 1)]
    if "|" in key:
        return key.split("|", 1)[0]
    return key

def _infer_search_path(cache_type: str, key: str, entry: Dict[str, Any] | None) -> str:
    """推導索引要寫入的 path 欄位。

    優先使用 entry 內明確提供的 path；若缺少，再依 cache_type 與 key 形態推導。
    """
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

def _infer_search_mod(
    cache_type: str, key: str, path: str, entry: Dict[str, Any] | None
) -> str:
    """推導索引要寫入的 mod 欄位。

    規則依序為：entry 明確值 > 路徑 anchor(`assets`/`data`) > 類型特定 fallback。
    """
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

def _build_search_metadata(
    cache_type: str, key: str, entry: Dict[str, Any] | None
) -> Dict[str, str]:
    """組合單筆索引所需的 metadata（mod/path）。"""
    path = _infer_search_path(cache_type, key, entry)
    mod = _infer_search_mod(cache_type, key, path, entry)
    return {"mod": mod, "path": path}

def build_index_entries(
    cache_type: str, cache_dict: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """把單一 cache_type 的記憶體字典轉成可批次索引的條目陣列（並行版本）。"""
    t0 = time.time()
    items = [(key, entry) for key, entry in cache_dict.items() if isinstance(entry, dict)]
    
    if not items:
        return []
    
    # 使用多執行緒並行處理 metadata 建立
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_build_single_entry, cache_type, key, entry): idx
            for idx, (key, entry) in enumerate(items)
        }
        results: List[Optional[Dict[str, Any]]] = [None] * len(items)
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception:
                pass
    
    elapsed = time.time() - t0
    log_debug(f"build_index_entries({cache_type}): {len(items)} entries in {elapsed:.2f}s")
    
    return [r for r in results if r is not None]

def _build_single_entry(cache_type: str, key: str, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """建立單筆索引條目（供並行呼叫）。"""
    return {
        "key": key,
        "src": entry.get("src", ""),
        "dst": entry.get("dst", ""),
        "type": cache_type,
        **_build_search_metadata(cache_type, key, entry),
    }

def rebuild_from_cache_dicts(
    engine: CacheSearchEngine,
    cache_types: List[str],
    cache_state: Dict[str, Dict[str, Any]],
) -> int:
    """依序重建多個類型的索引，回傳實際索引筆數（並行版本）。"""
    total_indexed = 0
    
    # 先並行處理所有 cache_type 的 entries 建立
    all_entries: Dict[str, List[Dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=len(cache_types)) as executor:
        futures = {
            executor.submit(build_index_entries, cache_type, cache_store.get_cache_type_dict(cache_state, cache_type)): cache_type
            for cache_type in cache_types
        }
        for future in as_completed(futures):
            cache_type = futures[future]
            entries = future.result()
            if entries:
                all_entries[cache_type] = entries
    
    # 再依序寫入 SQLite（保持原有寫入邏輯）
    for cache_type in cache_types:
        entries = all_entries.get(cache_type, [])
        if entries:
            engine.index_batch(entries)
            total_indexed += len(entries)
    
    return total_indexed

class SearchOrchestrator:
    """快取搜尋協調器。

    職責：管理搜尋引擎生命週期、提供重建索引流程，並封裝查詢入口。
    """

    def __init__(self, cache_root_getter: Callable[[], Path]):
        """以可注入的 cache root getter 建立協調器。"""
        self._cache_root_getter = cache_root_getter
        self._engine: Optional[CacheSearchEngine] = None
        self._lock = threading.RLock()

    def _db_path(self) -> Path:
        """回傳搜尋索引資料庫路徑，必要時先建立目錄。"""
        cache_root = self._cache_root_getter()
        cache_root.mkdir(parents=True, exist_ok=True)
        return cache_root / "search_index.db"

    def get_engine(self) -> Optional[CacheSearchEngine]:
        """取得（或延遲建立）共享搜尋引擎實例。"""
        with self._lock:
            if self._engine is None:
                self._engine = CacheSearchEngine(str(self._db_path()))
            return self._engine

    def rebuild_search_index(
        self, cache_types: List[str], cache_state: Dict[str, Dict[str, Any]]
    ) -> int:
        """以暫存檔重建整體索引，完成後原子替換正式索引檔。

        包含 Retry 機制：當檔案被鎖定時最多重試 3 次。
        """
        db_path = self._db_path()
        max_retries = 5
        retry_delay = 1.0  # 秒

        for attempt in range(max_retries):
            try:
                return self._do_rebuild_search_index(db_path, cache_types, cache_state)
            except PermissionError:
                if attempt < max_retries - 1:
                    import time
                    time.sleep(retry_delay)
                    continue
                else:
                    # 重試失敗，拋出原始錯誤
                    raise

    def _do_rebuild_search_index(
        self,
        db_path,
        cache_types: List[str],
        cache_state: Dict[str, Dict[str, Any]]
    ) -> int:
        """執行實際的索引重建作業。"""
        tmp_engine: Optional[CacheSearchEngine] = None
        old_engine: Optional[CacheSearchEngine] = None
        total_indexed = 0
        
        
        try:
            # 直接寫入目標資料庫（不使用 tmp 檔案，避免 Windows 檔案鎖問題）
            # 先關閉舊引擎
            with self._lock:
                old_engine = self._engine
                if old_engine is not None:
                    old_engine.close()
                    self._engine = None
            
            del old_engine
            import gc
            gc.collect()
            
            # 清理 WAL/SHM 檔案
            for suffix in ["-wal", "-shm"]:
                wal_file = db_path.with_name(db_path.name + suffix)
                if wal_file.exists():
                    try:
                        wal_file.unlink()
                    except Exception:
                        pass
            
            # 刪除舊資料庫重新建立
            if db_path.exists():
                try:
                    db_path.unlink()
                except Exception:
                    pass
            
            # 建立新引擎並直接寫入
            tmp_engine = CacheSearchEngine(str(db_path))
            total_indexed = rebuild_from_cache_dicts(
                tmp_engine, cache_types, cache_state
            )
            
            # 重新建立引擎
            with self._lock:
                self._engine = CacheSearchEngine(str(db_path))
            
            log_debug(f"索引重建完成: {total_indexed} 條")
            return total_indexed
        finally:
            if tmp_engine is not None:
                tmp_engine.close()

    def rebuild_search_index_for_type(
        self, cache_type: str, cache_state: Dict[str, Dict[str, Any]]
    ) -> int:
        """只重建單一 cache_type 的索引資料。"""
        engine = self.get_engine()
        if engine is None:
            return 0
        engine.clear_index_by_type(cache_type)
        cache_dict = cache_store.get_cache_type_dict(cache_state, cache_type)
        entries = build_index_entries(cache_type, cache_dict)
        if entries:
            engine.index_batch(entries)
        return len(entries)

    def search_cache(
        self,
        query: str,
        cache_type: str = None,
        limit: int = 50,
        use_fuzzy: bool = True,
    ) -> List[Dict]:
        """統一封裝查詢流程，必要時再做模糊重排序。"""
        engine = self.get_engine()
        if engine is None:
            return []
        results = engine.search(query, limit=limit, cache_type=cache_type)
        if use_fuzzy and results:
            matcher = FuzzyMatcher()
            results = matcher.rank_results(query, results)
        return results

    def find_similar_translations(
        self,
        text: str,
        cache_type: str = None,
        threshold: float = 0.6,
        limit: int = 20,
    ) -> List[Dict]:
        """先取候選，再以來源文字相似度過濾並截斷結果。"""
        candidates = self.search_cache(
            text, cache_type=cache_type, limit=limit * 2, use_fuzzy=False
        )
        if not candidates:
            return []
        matcher = FuzzyMatcher()
        similar = matcher.find_similar(
            text, candidates, threshold=threshold, key_field="src"
        )
        return similar[:limit]

    def close(self):
        """關閉並釋放協調器持有的搜尋引擎連線。"""
        with self._lock:
            if self._engine is not None:
                self._engine.close()
                self._engine = None
