"""app.services_impl.cache.cache_services

提供給 CacheView 的 cache UI service wrappers。

PR16 範圍：
- 從 `app/services.py` 抽離 cache UI services（函式名稱與行為保持一致）。
- 不改 `translation_tool.utils.cache_manager` 的內部實作（包含 PR12 search orchestration）。

維護注意：
- 本模組不應 import views（避免 circular import）。
- 回傳格式/欄位名屬於 UI contract（cache_view.py 會依賴），不可隨意更動。
"""

from __future__ import annotations

from typing import Any, Dict

from translation_tool.utils import cache_manager
from translation_tool.utils.log_unit import log_error, log_warning


def cache_get_overview_service() -> Dict[str, Any]:
    """取得目前所有翻譯快取（cache）的整體概覽資訊。"""

    return cache_manager.get_cache_overview()


def cache_reload_service() -> Dict[str, Any]:
    """重新載入翻譯快取，並重建全域搜尋索引。"""

    cache_manager.reload_translation_cache()
    cache_manager.rebuild_search_index()
    return cache_manager.get_cache_overview()


def cache_reload_type_service(cache_type: str) -> Dict[str, Any]:
    """只重新載入單一 cache_type，並重建該分類搜尋索引。"""

    cache_manager.reload_translation_cache_type(cache_type)
    cache_manager.rebuild_search_index_for_type(cache_type)
    return cache_manager.get_cache_overview()


def cache_save_all_service(
    write_new_shard: bool = True,
    only_types: list[str] | None = None,
) -> Dict[str, Any]:
    """將目前記憶體中的翻譯快取寫回磁碟。"""

    targets = only_types or list(cache_manager.CACHE_TYPES)

    for cache_type in targets:
        cache_manager.save_translation_cache(
            cache_type,
            write_new_shard=write_new_shard,
        )

    return cache_manager.get_cache_overview()


def cache_search_service(
    cache_type: str,
    query: str,
    mode: str = "key",
    limit: int = 5000,
) -> Dict[str, Any]:
    """在指定的翻譯快取中搜尋條目（FTS5 + fallback 線性掃描）。

    注意：此函式回傳格式是 UI contract；不得更動欄位名。
    """

    q = (query or "").strip()
    if not q:
        return {"items": [], "truncated": False, "limit": limit}

    try:
        results = cache_manager.search_cache(
            query=q,
            cache_type=cache_type,
            limit=limit,
            use_fuzzy=True,
        )

        if results:
            hits = []
            for r in results:
                if mode == "key":
                    if q.lower() not in r.get("src", "").lower():
                        continue
                elif mode == "dst":
                    if q.lower() not in r.get("dst", "").lower():
                        continue

                def _rank(text: str) -> int:
                    t = (text or "").lower()
                    if t == q.lower():
                        return 0
                    if t.startswith(q.lower()):
                        return 1
                    return 2

                rank_text = r.get("dst", "") if mode == "dst" else r.get("src", "")

                hits.append(
                    {
                        "key": r.get("key", ""),
                        "rank": _rank(rank_text),
                        "preview": str(r.get("dst", ""))[:40],
                        "score": r.get("combined_score", r.get("score", 0.0)),
                    }
                )

            hits.sort(key=lambda x: (x["rank"], -x["score"]))
            truncated = len(results) >= limit
            return {
                "items": hits,
                "truncated": truncated,
                "limit": limit,
            }

    except Exception as e:
        log_warning(f"搜尋引擎失敗，降級使用線性掃描: {e}")

    cache_ref = cache_manager.get_cache_dict_ref(cache_type)
    if not cache_ref:
        return {"items": [], "truncated": False, "limit": limit}

    q_lower = q.lower()
    hits: list[dict] = []
    truncated = False

    def _rank(text: str) -> int:
        t = (text or "").lower()
        if t == q_lower:
            return 0
        if t.startswith(q_lower):
            return 1
        return 2

    for key, entry in cache_ref.items():
        if not isinstance(entry, dict):
            continue

        dst = entry.get("dst", "")

        if mode == "dst":
            hay = (dst or "").lower()
            if q_lower not in hay:
                continue
            hits.append(
                {
                    "key": key,
                    "rank": _rank(dst),
                    "preview": str(dst)[:40],
                    "score": 0.5,
                }
            )
        else:
            hay = (key or "").lower()
            if q_lower not in hay:
                continue
            hits.append(
                {
                    "key": key,
                    "rank": _rank(key),
                    "preview": "",
                    "score": 0.5,
                }
            )

        if len(hits) >= limit:
            truncated = True
            break

    return {
        "items": hits,
        "truncated": truncated,
        "limit": limit,
    }


def cache_get_entry_service(cache_type: str, key: str) -> Dict[str, Any] | None:
    """取得指定 cache_type 中的單筆快取條目。"""

    return cache_manager.get_cache_entry(cache_type, key)


def cache_update_dst_service(cache_type: str, key: str, new_dst: str) -> bool:
    """更新指定快取條目的翻譯結果（dst）；僅更新記憶體快取，未寫回磁碟。"""

    entry = cache_manager.get_cache_entry(cache_type, key)
    if not entry:
        return False

    src = entry.get("src", "")
    cache_manager.add_to_cache(cache_type, key, src, new_dst)
    return True


def cache_rotate_service(cache_type: str) -> bool:
    """強制對指定 cache_type 進行 shard rotation。"""

    return cache_manager.force_rotate_shard(cache_type)


def cache_rebuild_index_service() -> Dict[str, Any]:
    """重建快取搜尋索引（A3 改進功能）。"""

    try:
        cache_manager.rebuild_search_index()

        total = sum(len(cache_manager.get_cache_dict_ref(ct)) for ct in cache_manager.CACHE_TYPES)

        return {
            "success": True,
            "total_indexed": total,
            "message": f"✅ 搜尋索引重建完成，共索引 {total:,} 條翻譯",
            "error": None,
        }

    except Exception as e:
        log_error(f"重建搜尋索引失敗: {e}")
        return {
            "success": False,
            "total_indexed": 0,
            "message": "重建索引失敗",
            "error": str(e),
        }
