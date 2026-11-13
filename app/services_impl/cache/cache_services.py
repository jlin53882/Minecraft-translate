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

from collections.abc import Callable
from typing import Any

from translation_tool.utils import cache_manager
from translation_tool.utils.log_unit import log_error, log_warning

# (已完成步數, 總步數, 說明)；只代表真實完成量。
ProgressCallback = Callable[[int, int, "str | None"], None]


class CacheSaveError(RuntimeError):
    """Raised when one or more cache types were not durably written."""

    def __init__(self, cache_types: list[str], overview: dict[str, Any] | None = None):
        self.cache_types = tuple(cache_types)
        self.overview = overview
        labels = ", ".join(self.cache_types) or "unknown"
        super().__init__(f"快取保存失敗：{labels}")


def cache_get_overview_service() -> dict[str, Any]:
    """取得目前所有翻譯快取（cache）的整體概覽資訊。"""

    return cache_manager.get_cache_overview()


class _MonotonicProgress:
    """把多階段進度合併成單一 (current, total, message)，且 current 只增不減。

    搜尋索引重建遇到檔案鎖會重試，階段內的 current 可能從頭開始；這裡忽略會讓進度倒退的回報。
    回呼可能在持有 cache_lock 的執行緒上被呼叫，``on_progress`` 只能轉送到 UI，不可阻塞。
    """

    def __init__(self, on_progress: ProgressCallback | None, total: int):
        self._on_progress = on_progress
        self._total = max(1, total)
        self._last = -1

    def stage(self, offset: int, stage_total: int) -> ProgressCallback:
        def report(current: int, _total: int, message: str | None = None) -> None:
            self.report(offset + min(current, stage_total), message)

        return report

    def report(self, current: int, message: str | None = None) -> None:
        if self._on_progress is None or current <= self._last:
            return
        self._last = current
        self._on_progress(current, self._total, message)


def cache_reload_service(on_progress: ProgressCallback | None = None) -> dict[str, Any]:
    """重新載入翻譯快取，並重建全域搜尋索引。

    ``on_progress(current, total, message)`` 回報真實進度：先逐個快取類型載入，
    再逐個快取類型重建搜尋索引，共 ``2 * len(CACHE_TYPES)`` 步。
    """

    type_count = len(cache_manager.CACHE_TYPES)
    progress = _MonotonicProgress(on_progress, type_count * 2)
    if on_progress is None:
        cache_manager.reload_translation_cache()
        indexed = cache_manager.rebuild_search_index()
    else:
        cache_manager.reload_translation_cache(
            on_progress=progress.stage(0, type_count)
        )
        indexed = cache_manager.rebuild_search_index(
            on_progress=progress.stage(type_count, type_count)
        )
    if not indexed:
        raise RuntimeError("快取未成功初始化，搜尋索引未重建")
    return cache_manager.get_cache_overview()


def cache_reload_type_service(cache_type: str) -> dict[str, Any]:
    """只重新載入單一 cache_type，並重建該分類搜尋索引。"""

    cache_manager.reload_translation_cache_type(cache_type)
    if not cache_manager.rebuild_search_index_for_type(cache_type):
        raise RuntimeError(f"快取未成功初始化，{cache_type} 搜尋索引未重建")
    return cache_manager.get_cache_overview()


def cache_save_all_service(
    write_new_shard: bool = True,
    only_types: list[str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """將目前記憶體中的翻譯快取寫回磁碟；每寫完一個類型回報 ``on_progress``。

    ``save_translation_cache`` uses an explicit boolean result to distinguish a
    durable write from a rejected/failed write.  Do not turn a false result
    into a successful UI action: callers rely on this service raising so that
    history is appended only after the shard write has completed.
    """

    targets = only_types or list(cache_manager.CACHE_TYPES)
    failed_types: list[str] = []

    for index, cache_type in enumerate(targets, start=1):
        saved = cache_manager.save_translation_cache(
            cache_type,
            write_new_shard=write_new_shard,
        )
        if saved is False:
            failed_types.append(str(cache_type))
        if on_progress is not None:
            on_progress(index, len(targets), f"已儲存 {cache_type}")

    if failed_types:
        try:
            overview = cache_manager.get_cache_overview()
        except Exception:  # noqa: BLE001 - preserve the original save failure
            overview = None
        raise CacheSaveError(failed_types, overview=overview)

    return cache_manager.get_cache_overview()


def _search_rank(text: str, q_lower: str) -> int:
    """計算搜尋排序用的 rank（越小代表越符合 query）。

    規則（case-insensitive）：
    - 0：text 完全等於 query
    - 1：text 以 query 開頭（prefix match）
    - 2：其他（fallback）
    """
    t = (text or "").lower()
    if t == q_lower:
        return 0
    if t.startswith(q_lower):
        return 1
    return 2


def _search_with_engine(
    cache_type: str, q: str, mode: str, limit: int
) -> dict[str, Any] | None:
    """用搜尋引擎（FTS5）查詢；沒有結果時回傳 None（交給線性掃描）。"""
    results = cache_manager.search_cache(
        query=q,
        cache_type=cache_type,
        limit=limit,
        use_fuzzy=True,
    )
    if not results:
        return None

    q_lower = q.lower()
    hits = []
    for r in results:
        if mode == "key":
            if q_lower not in r.get("src", "").lower():
                continue
        elif mode == "dst" and q_lower not in r.get("dst", "").lower():
            continue

        rank_text = r.get("dst", "") if mode == "dst" else r.get("src", "")
        hits.append(
            {
                "key": r.get("key", ""),
                "rank": _search_rank(rank_text, q_lower),
                "preview": str(r.get("dst", ""))[:40],
                "score": r.get("combined_score", r.get("score", 0.0)),
            }
        )

    hits.sort(key=lambda x: (x["rank"], -x["score"]))
    return {
        "items": hits,
        "truncated": len(results) >= limit,
        "limit": limit,
    }


def _search_linear(cache_type: str, q: str, mode: str, limit: int) -> dict[str, Any]:
    """線性掃描記憶體中的快取（搜尋引擎失敗或沒有結果時的降級路徑）。"""
    cache_ref = cache_manager.get_cache_dict_ref(cache_type)
    if not cache_ref:
        return {"items": [], "truncated": False, "limit": limit}

    q_lower = q.lower()
    hits: list[dict] = []
    truncated = False

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
                    "rank": _search_rank(dst, q_lower),
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
                    "rank": _search_rank(key, q_lower),
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


def cache_search_service(
    cache_type: str,
    query: str,
    mode: str = "key",
    limit: int = 5000,
) -> dict[str, Any]:
    """在指定的翻譯快取中搜尋條目（FTS5 + fallback 線性掃描）。

    注意：此函式回傳格式是 UI contract；不得更動欄位名。
    """

    q = (query or "").strip()
    if not q:
        return {"items": [], "truncated": False, "limit": limit}

    try:
        result = _search_with_engine(cache_type, q, mode, limit)
        if result is not None:
            return result
    except Exception as e:  # noqa: BLE001
        log_warning(f"搜尋引擎失敗，降級使用線性掃描: {e!r}")

    return _search_linear(cache_type, q, mode, limit)


def cache_get_entry_service(cache_type: str, key: str) -> dict[str, Any] | None:
    """取得指定 cache_type 中的單筆快取條目。"""

    return cache_manager.get_cache_entry(cache_type, key)


def cache_update_dst_service(cache_type: str, key: str, new_dst: str) -> bool:
    """更新指定快取條目的翻譯結果（dst）；僅更新記憶體快取，未寫回磁碟。"""

    entry = cache_manager.get_cache_entry(cache_type, key)
    if not entry:
        return False

    src = entry.get("src", "")
    return cache_manager.add_to_cache(cache_type, key, src, new_dst)


def cache_rotate_service(cache_type: str) -> bool:
    """強制對指定 cache_type 進行 shard rotation。"""

    return cache_manager.force_rotate_shard(cache_type)


def cache_rebuild_index_service(
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """重建快取搜尋索引（A3 改進功能）；逐個快取類型索引完成後回報 ``on_progress``。"""

    try:
        progress = _MonotonicProgress(on_progress, len(cache_manager.CACHE_TYPES))
        if on_progress is None:
            rebuilt = cache_manager.rebuild_search_index()
        else:
            rebuilt = cache_manager.rebuild_search_index(
                on_progress=progress.stage(0, len(cache_manager.CACHE_TYPES))
            )
        if not rebuilt:
            return {
                "success": False,
                "total_indexed": 0,
                "message": "重建索引失敗",
                "error": "快取未成功初始化，已拒絕重建以保留既有搜尋索引",
            }

        total = sum(
            len(cache_manager.get_cache_dict_ref(ct))
            for ct in cache_manager.CACHE_TYPES
        )

        return {
            "success": True,
            "total_indexed": total,
            "message": f"✅ 搜尋索引重建完成，共索引 {total:,} 筆翻譯",
            "error": None,
        }

    except Exception as e:  # noqa: BLE001
        log_error(f"重建搜尋索引失敗: {e!r}")
        return {
            "success": False,
            "total_indexed": 0,
            "message": "重建索引失敗",
            "error": str(e),
        }
