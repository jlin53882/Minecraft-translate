"""settings.py

讀取 ``translation_db`` 設定並開啟資料庫。翻譯流程與介面共用同一個入口，
資料庫缺檔或無法開啟時回傳 None（功能靜默停用，不影響其他流程）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.schema import (
    CUSTOM_SOURCE_BASE,
    DB_EMPTY,
    DB_FOREIGN,
    DB_NEWER,
    DB_VALID,
    DEFAULT_PRIORITY,
    classify_database,
)
from translation_tool.translation_db.source_catalog import (
    DEFAULT_SOURCE_CATALOG,
    SourceCatalog,
)
from translation_tool.utils.log_unit import log_info, log_warning
from translation_tool.utils.path_text import normalize_path_text, strip_path_quotes

DEFAULT_DB_FILE = "mod_translation.db"


@dataclass(frozen=True)
class DbSettings:
    """``translation_db`` 設定的快照。"""

    enabled: bool = True
    merge_enabled: bool = (
        True  # 語系合併是否以資料庫補譯（與機器翻譯的 enabled 各自獨立）
    )
    path: str = ""  # 空白 = 資料目錄內的預設檔名；首次建立資料庫時會寫入實際路徑
    version: str = ""
    cross_version: bool = True
    write_back: bool = True
    sync_manual: bool = True
    priority: tuple[int, ...] = DEFAULT_PRIORITY
    priority_lines: tuple[
        str, ...
    ] = ()  # 設定裡「來源優先順序」輸入的名稱（含新增的自訂來源）
    zip_source: int | None = None  # 翻譯 ZIP 匯入時，「譯文來源標記」的預設來源
    source_catalog: SourceCatalog = field(
        default_factory=lambda: DEFAULT_SOURCE_CATALOG
    )

    @property
    def usable(self) -> bool:
        """已啟用且指定了遊戲版本（沒有版本無法判斷寫回哪一版）。"""
        return self.enabled and bool(self.version.strip())

    def resolved_path(self) -> Path:
        from translation_tool.utils.config_manager import resolve_project_path

        path = resolve_project_path(self.path or DEFAULT_DB_FILE)
        # 填的是資料夾：使用該資料夾內的預設檔名
        return path / DEFAULT_DB_FILE if path.is_dir() else path


# 路徑文字整理共用 utils.path_text（資料庫路徑、各頁路徑欄位、路徑解析同一套規則）
normalize_db_path = normalize_path_text
strip_quotes = strip_path_quotes


def describe_db_path(path_text: object) -> tuple[str, str, Path]:
    """檢查使用者填的資料庫路徑，回傳 ``(等級, 說明, 實際檔案路徑)``。

    等級：``ok`` 找到可用的資料庫／``info`` 檔案尚未建立但位置可用／``warn`` 有問題。
    輸入欄位邊打邊呼叫（唯讀檢查，不會建立或修改任何檔案）。
    """
    settings = DbSettings(path=normalize_db_path(path_text))
    path = settings.resolved_path()
    blank = not settings.path
    if path.is_file():
        kind = classify_database(path)
        if kind == DB_FOREIGN:
            return (
                "warn",
                f"檔案存在，但不是 Mod 翻譯資料庫（為避免污染不會使用）：{path}",
                path,
            )
        if kind == DB_NEWER:
            return "warn", f"資料庫版本比本程式新，請更新程式後再使用：{path}", path
        return "ok", f"✓ 找到資料庫：{path}", path
    if blank:
        return "info", f"空白：使用資料目錄內的預設位置（尚未建立）：{path}", path
    if not path.parent.is_dir():
        return "warn", f"找不到這個檔案，連資料夾也不存在：{path.parent}", path
    return "warn", f"找不到資料庫檔案（資料夾存在）：{path}", path


MAX_SOURCE_NAME = 30


def clean_source_names(names: Any) -> list[str]:
    """整理使用者輸入的來源名稱：去前後空白、略過空行與重複、限制長度。"""
    out: list[str] = []
    for raw in names or []:
        name = str(raw).strip()[:MAX_SOURCE_NAME]
        if name and name not in out:
            out.append(name)
    return out


_registry_cache: dict[Path, tuple[tuple, dict[str, int]]] = {}


def _file_signature(path: Path) -> tuple:
    """資料庫檔（含 WAL）的修改時間與大小；登錄表只在寫入時才會變。"""
    sig = []
    for candidate in (path, path.with_name(path.name + "-wal")):
        try:
            st = candidate.stat()
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    return tuple(sig)


def read_custom_sources(path: Path) -> dict[str, int]:
    """讀取資料庫登錄的自訂來源 ``{名稱: 代碼}``（唯讀；檔案不存在或讀不到回傳空）。

    設定每次被讀取都會呼叫，所以以檔案簽章快取，資料庫沒變動就不重新開檔。
    """
    if not path.is_file():
        return {}
    sig = _file_signature(path)
    cached = _registry_cache.get(path)
    if cached is not None and cached[0] == sig:
        return dict(cached[1])
    registry = _read_custom_sources_uncached(path)
    _registry_cache[path] = (sig, registry)
    return dict(registry)


def _read_custom_sources_uncached(path: Path) -> dict[str, int]:
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'custom_sources'"
            ).fetchone()
        finally:
            conn.close()
        data = json.loads(row[0]) if row else {}
    except (sqlite3.Error, ValueError, OSError):
        return {}
    return {str(k): int(v) for k, v in data.items() if isinstance(v, int)}


def ensure_custom_sources(path: Path, names: list[str]) -> dict[str, int]:
    """把還沒登錄的新名稱登錄為自訂來源（配發新代碼，寫入資料庫 meta）；回傳完整登錄表。

    只處理「本功能的資料庫」；名稱與內建來源同名者沿用內建代碼，不另外配發。
    """
    registry = read_custom_sources(path)
    catalog = SourceCatalog.from_registry(registry)
    new = []
    for name in clean_source_names(names):
        if catalog.resolve(name) is not None:
            continue
        if name.startswith(("builtin:", "custom:")):
            continue
        new.append(name)
    if not new or not path.is_file() or classify_database(path) != DB_VALID:
        return registry
    try:
        conn = sqlite3.connect(path, timeout=5)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'custom_sources'"
            ).fetchone()
            registry = (
                {str(k): int(v) for k, v in json.loads(row[0]).items()} if row else {}
            )
            next_code = max([CUSTOM_SOURCE_BASE - 1, *registry.values()]) + 1
            for name in new:
                if name not in registry:
                    registry[name] = next_code
                    next_code += 1
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('custom_sources', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(registry, ensure_ascii=False),),
            )
            conn.commit()
        finally:
            conn.close()
        log_info(f"📚 新增自訂來源：{'、'.join(new)}")
    except (sqlite3.Error, ValueError) as exc:
        log_warning(f"⚠️ 無法登錄自訂來源 {new}：{exc!r}")
        return read_custom_sources(path)
    return registry


def preview_new_source_names(text: str) -> tuple[list[str], list[str]]:
    """設定頁輸入框的即時預覽：``(已存在, 將新增)`` 的來源名稱（只讀，不登錄）。"""
    registry = read_custom_sources(load_db_settings().resolved_path())
    return split_new_source_names(text.splitlines(), registry)


def parse_priority(names: Any, custom: dict[str, int] | None = None) -> tuple[int, ...]:
    """把來源名稱清單轉成代碼優先序。

    名稱對得上內建來源或已登錄的自訂來源（``custom``）才有效；
    沒列出的內建來源依預設順序、沒列出的自訂來源依代碼順序接在後面。
    """
    catalog = SourceCatalog.from_registry(custom)
    order: list[int] = []
    for name in clean_source_names(names):
        code = catalog.resolve(name)
        if code is not None and code not in order:
            order.append(code)
    order.extend(c for c in DEFAULT_PRIORITY if c not in order)
    order.extend(c for c in sorted((custom or {}).values()) if c not in order)
    return tuple(order)


def priority_names(
    priority: tuple[int, ...], catalog: SourceCatalog = DEFAULT_SOURCE_CATALOG
) -> list[str]:
    return [catalog.label(code) for code in priority]


def split_new_source_names(
    names: Any, registry: dict[str, int] | None = None
) -> tuple[list[str], list[str]]:
    """輸入的名稱分成 ``(已存在, 將新增)``；供設定頁即時提示。"""
    catalog = SourceCatalog.from_registry(registry)
    cleaned = clean_source_names(names)
    known = [n for n in cleaned if catalog.resolve(n) is not None]
    new = [
        n
        for n in cleaned
        if catalog.resolve(n) is None and not n.startswith(("builtin:", "custom:"))
    ]
    return known, new


def load_db_settings(config: dict | None = None) -> DbSettings:
    """由設定檔建立 ``DbSettings``（缺欄位用預設）。"""
    if config is None:
        from translation_tool.utils.config_manager import load_config

        config = load_config()
    cfg = config.get("translation_db", {}) or {}
    path = normalize_db_path(cfg.get("path"))
    lines = tuple(clean_source_names(cfg.get("priority")))
    registry = ensure_custom_sources(DbSettings(path=path).resolved_path(), list(lines))
    catalog = SourceCatalog.from_registry(registry)
    return DbSettings(
        enabled=bool(cfg.get("enabled", True)),
        merge_enabled=bool(cfg.get("merge_enabled", True)),
        path=path,
        version=str(cfg.get("version") or "").strip(),
        cross_version=bool(cfg.get("cross_version", True)),
        write_back=bool(cfg.get("write_back", True)),
        sync_manual=bool(cfg.get("sync_manual", True)),
        priority=parse_priority(lines, registry),
        priority_lines=lines,
        zip_source=catalog.resolve(cfg.get("zip_source")),
        source_catalog=catalog,
    )


_open_lock = threading.Lock()


def database_problem(settings: DbSettings) -> str:
    """設定的資料庫檔案「存在但不能用」的原因（正常或尚未建立回傳空字串）。

    供介面顯示：開不起來時不能讓使用者看到「尚未建立資料庫」而去重新掃描
    （掃描一樣會被拒絕，還會誤以為是自己操作錯誤）。
    """
    path = settings.resolved_path()
    if not path.is_file():
        return ""
    kind = classify_database(path)
    if kind == DB_FOREIGN:
        return f"設定的檔案不是 Mod 翻譯資料庫（為避免污染不會使用）：{path}。請到設定頁改用其他路徑。"
    if kind == DB_NEWER:
        return f"資料庫版本比本程式新，請更新程式後再使用：{path}"
    return ""


def remember_db_path(settings: DbSettings, path: Path) -> bool:
    """設定的資料庫路徑是空白時，把實際建立的路徑寫進 config.json（設定頁會顯示）。

    只在路徑空白時寫入，不會覆蓋使用者自己填的路徑；寫入失敗不影響資料庫本身。
    """
    if settings.path.strip():
        return False
    try:
        from translation_tool.utils.config_manager import load_config, save_config

        config = load_config()
        block = config.setdefault("translation_db", {})
        if str(block.get("path") or "").strip():
            return False
        block["path"] = str(path)
        if save_config(config):
            log_info(f"📚 已把資料庫路徑寫入設定：{path}")
            return True
    except Exception as exc:  # noqa: BLE001 - 寫設定失敗不應中斷建立資料庫
        log_warning(f"⚠️ 無法把資料庫路徑寫入設定：{exc!r}")
    return False


def open_db(
    settings: DbSettings,
    *,
    create: bool = False,
    readonly: bool = False,
    sync_priority: bool | None = None,
) -> TranslationDB | None:
    """開啟設定指定的資料庫。

    ``create=False``（翻譯流程）：檔案不存在就回傳 None，不會憑空建立。
    ``create=True``（掃描／介面）：不存在則建立。
    ``readonly=True``（純查詢流程）：使用 SQLite 唯讀連線，不同步或改寫來源優先序。
    ``sync_priority=False`` 可供使用固定任務快照、但仍需寫回資料的流程使用：
    不將該快照套用到共用 ``meta.priority`` / ``effective``。新建資料庫仍會初始化優先序。
    """
    if readonly and create:
        raise ValueError("唯讀資料庫不可同時要求建立")
    if readonly and sync_priority:
        raise ValueError("唯讀資料庫不可同步來源優先序")
    path = settings.resolved_path()
    with _open_lock:
        if not path.is_file() and not create:
            log_info(f"📚 預翻譯資料庫尚未建立（{path}），已略過")
            return None
        existed = path.is_file()
        try:
            initialize_priority = create and (
                not existed or classify_database(path) == DB_EMPTY
            )
            should_sync_priority = (
                (not readonly) if sync_priority is None else sync_priority
            )
            # 對新資料庫，meta.priority 尚無既有全域設定可保護，必須寫入初始值。
            should_sync_priority = should_sync_priority or initialize_priority
            db = TranslationDB(
                path,
                priority=settings.priority,
                readonly=readonly,
                create=create,
                sync_priority=should_sync_priority,
            )
            if create and not existed and path.is_file():
                remember_db_path(settings, path)
                # 資料庫剛建立：設定裡輸入的新名稱現在才能登錄成自訂來源
                registry = ensure_custom_sources(path, list(settings.priority_lines))
                if registry:
                    db.set_priority(parse_priority(settings.priority_lines, registry))
            return db
        except Exception as exc:  # noqa: BLE001 - 資料庫問題不應中斷翻譯
            log_warning(f"⚠️ 預翻譯資料庫無法開啟，已略過：{path}（{exc!r}）")
            return None
