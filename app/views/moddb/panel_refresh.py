"""Background data snapshots for Mod DB tabs."""

from __future__ import annotations

from dataclasses import replace

from app.services_impl.moddb_service import open_database, version_choices
from app.services_impl.moddb_source_service import source_catalog_for
from app.views.moddb.entries_panel import PAGE_SIZE

ALL_MODS = "__all__"


def load_panel_snapshot(key: str, request: dict, settings) -> dict:
    """Read tab data using a worker-owned connection; return values for UI apply."""
    db = open_database(create=False, settings=settings)
    snapshot = {"key": key, "identity": None, "error": None}
    try:
        snapshot["identity"] = _database_identity(db)
        snapshot["source_catalog"] = source_catalog_for(db)
        if key == "entries":
            return _load_entries_snapshot(db, request, settings, snapshot)
        if key == "scan":
            snapshot["versions"] = version_choices(db)
            snapshot["database_versions"] = set(db.versions()) if db else set()
            return snapshot
        if key == "translate":
            return _load_translate_snapshot(db, request, snapshot)
        raise ValueError(f"未知 Mod DB 頁籤：{key}")
    finally:
        if db is not None:
            db.close()


def _load_entries_snapshot(db, request: dict, settings, snapshot: dict) -> dict:
    versions = db.versions() if db else []
    requested_version = request.get("version")
    version = requested_version if requested_version in versions else None
    if version is None and settings.version in versions:
        version = settings.version
    if version is None and versions:
        version = versions[0]
    mods = db.mods(version) if db and version else []
    kinds = db.kinds(version) if db and version else []
    mod_id = request.get("mod_id") if request.get("mod_id") in mods else None
    kind = request.get("kind") if request.get("kind") in kinds else None
    rows, total, error, detail = [], 0, None, None
    if db is not None and version:
        criteria = replace(
            request["criteria"], version=version, mod_id=mod_id, kind=kind
        )
        try:
            rows, total = db.list_entries(criteria=criteria, limit=PAGE_SIZE, offset=0)
        except ValueError as exc:
            error = str(exc)
        selected_id = request.get("selected_id")
        row_ids = {row.id for row in rows}
        target_id = (
            selected_id if selected_id in row_ids else (rows[0].id if rows else None)
        )
        detail = db.entry_detail(target_id) if target_id is not None else None
    snapshot.update(
        versions=versions,
        version=version,
        mods=mods,
        kinds=kinds,
        mod_id=mod_id,
        kind=kind,
        rows=rows,
        total=total,
        list_error=error,
        detail=detail,
    )
    return snapshot


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
    snapshot.update(
        versions=versions,
        version=version,
        mods=mods,
        mod=mod,
        missing=missing,
        reusable=reusable,
    )
    return snapshot


def _database_identity(db):
    if db is None:
        return None
    path = getattr(db, "path", None)
    try:
        stat = path.stat()
        return str(path.resolve()), stat.st_dev, stat.st_ino
    except (AttributeError, OSError):
        return str(path), id(db)
