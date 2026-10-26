"""圖示預覽的冷載入掃描：JAR 目錄 → 逐步累積的來源（模組清單先出現，不必等全部完成）。

整個掃描（開 JAR、讀 lang、解析圖示、寫 L2 快取）都在背景執行緒／行程池；
畫面只在 event loop 上收到「目前累積結果的快照」，不做任何解析或檔案 I/O。
"""

import time

from app.icon_scan_pool import stream_jar_scans
from app.views.icon_preview.entries_cache import (
    _compute_source_identity,
    save_source_cache_l2,
)
from app.views.icon_preview.mod_store import ModEntryStore, ScannedSource
from app.views.icon_preview.progress import _make_progress_callback
from translation_tool.utils.cancellation import is_cancelled, raise_if_cancelled
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_info, log_warning
from translation_tool.utils.worker_count import resolve_worker_count

# 部分結果送到畫面的最短間隔（秒）：每次都會重畫第一頁模組列。
PARTIAL_INTERVAL_SEC = 0.5


def _load_prebuilt_index(source_root) -> dict | None:
    """已建好且與目前 JAR 一致的圖示索引（``tools/build_icon_index.py``）；沒有則回傳 None。"""
    from app import icon_index

    try:
        return icon_index.load_icon_index(source_root)
    except Exception as exc:  # noqa: BLE001 - 預建索引載入失敗時改為逐 JAR 解析，但要留下紀錄
        log_warning(f"[IconPreview] 載入預建圖示索引失敗，改為逐 JAR 解析：{exc!r}")
        return None


def scan_jar_directory(view, generation: int | None = None) -> ModEntryStore:
    """掃描 ``view.source_root`` 的所有 JAR，回傳可直接顯示的 ``ModEntryStore``（背景執行緒）。

    每個 JAR 只開一次：同時讀出 ``en_us.json`` 並解析圖示。完成後寫入 L2 快取。
    掃描途中每隔 ``PARTIAL_INTERVAL_SEC`` 把目前累積的快照交給畫面。
    """
    root = view.source_root
    if root is None:
        return ModEntryStore(ScannedSource(), {})
    started = time.perf_counter()
    identity = _compute_source_identity(root, "jar_directory")
    jars = sorted(root.glob("*.jar"), key=lambda path: path.name.casefold())
    icon_index = _load_prebuilt_index(root)
    progress = _make_progress_callback(
        view, "掃描模組與圖示", len(jars), generation=generation
    )
    progress(0, len(jars))

    builder = ScannedSource(identity)
    last_publish = time.monotonic()
    for done, scan in enumerate(
        stream_jar_scans(
            root,
            jars,
            workers=resolve_worker_count("icon_cache", load_config()),
            should_cancel=is_cancelled,
            with_icons=icon_index is None,
        ),
        start=1,
    ):
        builder.add(scan, icon_index)
        progress(done, len(jars))
        if time.monotonic() - last_publish >= PARTIAL_INTERVAL_SEC:
            last_publish = time.monotonic()
            publish_partial(view, generation, builder.snapshot())

    source = builder.snapshot()
    scanned = time.perf_counter()
    raise_if_cancelled()
    zh_lookup = view._build_zh_tw_lookup(set(source.modids))
    raise_if_cancelled()
    save_source_cache_l2(root, source)
    log_info(
        f"[IconPreview] JAR 目錄掃描完成：{len(jars)} 個 JAR、{len(source.modids)} 個模組、"
        f"{source.entry_count} 筆（掃描 {scanned - started:.1f}s，"
        f"譯文與 L2 快取 {time.perf_counter() - scanned:.1f}s）"
    )
    return ModEntryStore(source, zh_lookup)


def publish_partial(view, generation: int | None, source) -> None:
    """（可在背景執行緒呼叫）把目前累積的快照交給 event loop 顯示；沒有 event loop 時略過。"""
    run_task = getattr(view.page, "run_task", None)
    if run_task is None:
        return
    view._partial_source = source
    if view._partial_pending:
        return  # 已排程：套用時會取最新的快照
    view._partial_pending = True

    async def _apply():
        view._partial_pending = False
        apply_partial(view, generation)

    run_task(_apply)


def apply_partial(view, generation: int | None) -> None:
    """（event loop 上）顯示累積中的模組清單；使用者正在看詳情或搜尋結果時不打擾。"""
    source = view._partial_source
    if source is None or not view._loading:
        return
    if generation is not None and generation != view._load_generation:
        return
    if view.current_modid is not None or view._mod_search_text.strip():
        return
    view._scan_partial = True
    view.mods = ModEntryStore(source, {})
    view._render_mod_list()
