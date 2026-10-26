"""Background data snapshots for Mod DB tabs."""

from __future__ import annotations

from dataclasses import replace

from app.services_impl.moddb_service import (
    database_data_generation,
    moddb_read_snapshot,
    version_choices,
)
from app.services_impl.moddb_source_service import (
    CUSTOM_SOURCE_BASE,
    source_catalog_for,
)
from app.views.moddb.entries_panel import PAGE_SIZE
from app.views.moddb.entry_filters import database_identity

ALL_MODS = "__all__"


def load_panel_snapshot(key: str, request: dict, settings) -> dict:
    """Read tab data from a worker-owned, read-only connection."""
    snapshot = {
        "key": key,
        "identity": None,
        "error": None,
        "keep_scroll": (
            bool(request.get("keep_scroll")) if key == "entries" else False
        ),
    }
    snapshot["effective_source_codes"] = ()
    with moddb_read_snapshot(settings) as db:
        snapshot["identity"] = _database_identity(db)
        snapshot["source_catalog"] = source_catalog_for(db)
        snapshot["db_generation"] = database_data_generation(db)
        if key == "entries":
            return _load_entries_snapshot(db, request, settings, snapshot)
        if key == "scan":
            snapshot["versions"] = version_choices(db)
            snapshot["database_versions"] = set(db.versions()) if db else set()
            return snapshot
        if key == "translate":
            return _load_translate_snapshot(db, request, snapshot)
        raise ValueError(f"未知 Mod DB 頁籤：{key}")


def _read_entries_page(db, criteria, request: dict) -> dict:
    """讀一頁條目、這頁的智慧校對標記與要顯示的詳情（頁碼超過最後一頁就退回最後一頁）。

    詳情選取規則：沿用 ``selected_id``；它不在這一頁時用 ``fallback_index``（儲存後
    選同一個位置的下一筆），都沒有就選第一筆。
    """
    page = max(1, int(request.get("page", 1)))
    rows, total, error, detail = [], 0, None, None
    marks: dict = {}
    try:
        rows, total = db.list_entries(
            criteria=criteria, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
        )
        last_page = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        if page > last_page:
            page = last_page
            rows, total = db.list_entries(
                criteria=criteria, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
            )
    except ValueError as exc:
        error = str(exc)
    row_ids = {row.id for row in rows}
    selected_id = request.get("selected_id")
    fallback_index = request.get("fallback_index")
    if selected_id in row_ids:
        target_id = selected_id
    elif rows and fallback_index is not None:
        target_id = rows[min(max(fallback_index, 0), len(rows) - 1)].id
    else:
        target_id = rows[0].id if rows else None
    if target_id is not None:
        detail = db.entry_detail(target_id)
    if error is None:
        marks = db.smart_review_marks(sorted(row_ids))
    return {
        "rows": rows,
        "total": total,
        "page": page,
        "list_error": error,
        "detail": detail,
        "smart_review_marks": marks,
    }


def _load_entries_snapshot(db, request: dict, settings, snapshot: dict) -> dict:
    scope = _resolve_entries_scope(db, request, settings)
    page_data = {
        "rows": [],
        "total": 0,
        "page": max(1, int(request.get("page", 1))),
        "list_error": None,
        "detail": None,
        "smart_review_marks": {},
    }
    if db is not None and scope["version"]:
        page_data = _read_entries_page(db, scope["criteria"], request)
    snapshot.update(
        page_data,
        versions=scope["versions"],
        version=scope["version"],
        mods=scope["mods"],
        kinds=scope["kinds"],
        effective_source_codes=scope["source_codes"],
        source=scope["source"],
        filter_options_are_filtered=scope["options_are_filtered"],
        mod_id=scope["mod_id"],
        kind=scope["kind"],
    )
    return snapshot


def load_entries_filter_snapshot(settings, request: dict) -> dict:
    """Query a filtered page on a worker-owned read-only SQLite snapshot."""
    with moddb_read_snapshot(settings) as db:
        return _entries_filter_snapshot(db, settings, request)


def _entries_filter_snapshot(db, settings, request: dict) -> dict:
    page_only = bool(request.get("page_only"))
    snapshot = {
        "key": "entries",
        "identity": _database_identity(db),
        "source_catalog": source_catalog_for(db),
        "db_generation": None,
        "filter_only": True,
        "filter_options_ready": not page_only,
        "keep_scroll": bool(request.get("keep_scroll")),
        "versions": [],
        "version": request.get("version"),
        "mods": [],
        "kinds": [],
        "effective_source_codes": (),
        "source": None,
        "filter_options_are_filtered": False,
        "mod_id": request.get("mod_id"),
        "kind": request.get("kind"),
        "page": max(1, int(request.get("page", 1))),
        "rows": [],
        "smart_review_marks": {},
        "total": 0,
        "list_error": None,
        "detail": None,
    }
    if db is None:
        return snapshot
    snapshot["db_generation"] = database_data_generation(db)
    if page_only:
        # 只換頁：篩選條件與下拉選項都沒變，不必重算版本／模組／來源選項。
        snapshot.update(_read_entries_page(db, request["criteria"], request))
        return snapshot
    scope = _resolve_entries_scope(db, request, settings)
    snapshot.update(
        _read_entries_page(db, scope["criteria"], request),
        versions=scope["versions"],
        version=scope["version"],
        mods=scope["mods"],
        kinds=scope["kinds"],
        effective_source_codes=scope["source_codes"],
        source=scope["source"],
        filter_options_are_filtered=scope["options_are_filtered"],
        mod_id=scope["mod_id"],
        kind=scope["kind"],
    )
    return snapshot


def _resolve_entries_scope(db, request: dict, settings) -> dict:
    """解析條目頁的版本、選值與依進階條件縮減後的下拉選項。"""
    versions = db.versions() if db else []
    requested_criteria = request["criteria"]
    requested_version = request.get("version") or requested_criteria.version
    version = requested_version if requested_version in versions else None
    if version is None and settings.version in versions:
        version = settings.version
    if version is None and versions:
        version = versions[0]

    available_mods = db.mods(version) if db and version else []
    kinds = db.kinds(version) if db and version else []
    mod_id = request.get("mod_id") if request.get("mod_id") in available_mods else None
    kind = request.get("kind") if request.get("kind") in kinds else None
    source_codes = db.effective_source_codes(version) if db and version else ()
    source = requested_criteria.source
    if (
        source is not None
        and source >= CUSTOM_SOURCE_BASE
        and source not in source_codes
    ):
        source = None

    criteria = replace(
        requested_criteria,
        version=version or requested_criteria.version,
        mod_id=mod_id,
        kind=kind,
        source=source,
    )
    # module/source 選單描述的是目前可達的結果 scope，不只是選用的日期／品質進階條件。
    # status、state、搜尋、kind 與固定條目條件都可能從結果中排除某個 module 或 source；
    # 若忽略這些條件，資料庫寫入後下拉選單就可能保留已沒有符合列的 module。
    options_are_filtered = bool(
        criteria.mod_id is not None
        or criteria.kind is not None
        or criteria.source is not None
        or criteria.state != "all"
        or criteria.query.strip()
        or criteria.review_status is not None
        or criteria.smart_review is not None
        or criteria.entry_ids is not None
        or criteria.include_ids is not None
        or bool(criteria.exclude_ids)
        or criteria.time is not None
        or criteria.quality.active
    )
    if options_are_filtered and db is not None and version:
        filtered_mods, filtered_sources = db.filter_option_values(criteria)
        # 選值在進階條件下沒有符合條目時，仍要保留原選值；
        # 清空它會把條件放寬成其他模組／來源，讓使用者看到錯誤範圍的結果。
        mods = sorted(set(filtered_mods) | ({mod_id} if mod_id is not None else set()))
        source_codes = tuple(
            sorted(set(filtered_sources) | ({source} if source is not None else set()))
        )
    else:
        mods = available_mods

    return {
        "versions": versions,
        "version": version,
        "mods": mods,
        "kinds": kinds,
        "source_codes": source_codes,
        "source": source,
        "mod_id": mod_id,
        "kind": kind,
        "criteria": criteria,
        "options_are_filtered": options_are_filtered,
    }


def _load_translate_snapshot(db, request: dict, snapshot: dict) -> dict:
    versions = db.versions() if db else []
    selected_version = request.get("version")
    version = (
        selected_version
        if selected_version in versions
        else (versions[0] if versions else None)
    )
    mods = db.mods(version) if db and version else []
    selected_mod = request.get("mod")
    mod = selected_mod if selected_mod == ALL_MODS or selected_mod in mods else ALL_MODS
    mod_ids = () if mod == ALL_MODS else (mod,)
    missing = db.count_untranslated(version, mod_ids) if db and version else 0
    reusable = (
        db.count_reusable(version, mod_ids)
        if db and version and request.get("reuse")
        else 0
    )
    # 背景刷新只更新快速計數；完整批次規劃由預覽／開始翻譯時的 service
    # 使用同一份 items、session 與 planner 計算，避免每次範圍變更都掃完整版本。
    snapshot.update(
        versions=versions,
        version=version,
        mods=mods,
        mod=mod,
        missing=missing,
        reusable=reusable,
        planned_items=None,
        planned_batches=None,
    )
    return snapshot


def _database_identity(db):
    return database_identity(db)
