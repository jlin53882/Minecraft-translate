"""圖示預覽冷載入：結果與逐 key 解析一致、逐步顯示部分結果、掃描不在 UI 執行緒。"""

import asyncio
import json
import threading
import zipfile

from app import icon_index
from app.icon_catalog import ModpackAssetCatalog
from app.icon_resolve import hit_to_uri, resolve_icon
from app.views.icon_preview import scan_operation
from app.views.icon_preview.entries_cache import (
    _compute_source_identity,
    open_entries_cache_l2,
)
from app.views.icon_preview.mod_store import ModEntryStore, ScannedSource
from app.views.icon_preview.scan_operation import (
    apply_partial,
    publish_partial,
    scan_jar_directory,
)
from tests.conftest import mock_page
from tests.test_icon_jar_scan import make_jar, pack


def make_view(source_root, review_root=None, *, page=None):
    from app.views.icon_preview_view import IconPreviewView

    view = IconPreviewView(page or mock_page())
    view.update = lambda *args, **kwargs: None
    view.source_root = source_root
    view.review_root = review_root
    return view


def entries_of(store) -> list:
    return [entry for modid in store for entry in store[modid]]


def test_cold_scan_icon_paths_equal_per_key_resolution(tmp_path):
    mods = pack(tmp_path)
    view = make_view(mods)

    store = scan_jar_directory(view, 0)

    catalog = ModpackAssetCatalog.from_mods_directory(mods)
    try:
        checked = 0
        for entry in entries_of(store):
            jar = mods / entry.source_jar
            with zipfile.ZipFile(jar) as zf:
                hit = resolve_icon(
                    jar, entry.modid, entry.key, catalog, current_zf=zf, budget=None
                )
            expected = hit_to_uri(hit) if hit else None
            assert getattr(entry, "icon_path", None) == expected, entry.key
            checked += 1
    finally:
        catalog.close()
    assert checked == store.total_entries == 7
    assert sum(1 for e in entries_of(store) if getattr(e, "icon_path", None)) == 5


def test_cold_scan_writes_l2_cache_that_reloads_identically(tmp_path):
    mods = pack(tmp_path)
    view = make_view(mods)

    store = scan_jar_directory(view, 0)

    identity = _compute_source_identity(mods, "jar_directory")
    reopened = open_entries_cache_l2(mods, identity)
    assert reopened is not None
    assert reopened.modids == store.source.modids
    for modid in store.source.modids:
        assert reopened.load(modid) == store.source.load(modid)


def test_cold_scan_applies_review_translations(tmp_path):
    mods = pack(tmp_path)
    review = tmp_path / "review" / "alpha" / "lang"
    review.mkdir(parents=True)
    (review / "zh_tw.json").write_text(
        json.dumps({"item.alpha.sword": "劍"}), encoding="utf-8"
    )

    store = scan_jar_directory(make_view(mods, tmp_path / "review"), 0)

    by_key = {e.key: e for e in store["alpha"]}
    assert by_key["item.alpha.sword"].zh_tw == "劍"
    assert by_key["item.alpha.plain"].zh_tw == ""


def test_scanned_source_is_independent_of_completion_order(tmp_path):
    mods = pack(tmp_path)
    from app.icon_jar_scan import scan_jar

    scans = [scan_jar(mods / name, None) for name in ("alpha-1.0.jar", "beta-2.0.jar")]
    # 同一個 modid 出現在兩個 JAR：依 JAR 名稱排序，與加入順序無關
    make_jar(
        mods / "alpha-addon.jar",
        {"assets/alpha/lang/en_us.json": json.dumps({"item.alpha.extra": "E"})},
    )
    scans.append(scan_jar(mods / "alpha-addon.jar", None))

    forward, backward = ScannedSource(), ScannedSource()
    for scan in scans:
        forward.add(scan)
    for scan in reversed(scans):
        backward.add(scan)

    assert forward.snapshot().load("alpha") == backward.snapshot().load("alpha")
    assert forward.snapshot().load("alpha")[0]["source_jar"] == "alpha-1.0.jar"


def test_scanned_source_snapshot_is_not_affected_by_later_adds(tmp_path):
    mods = pack(tmp_path)
    from app.icon_jar_scan import scan_jar

    builder = ScannedSource()
    builder.add(scan_jar(mods / "alpha-1.0.jar", None))
    snapshot = builder.snapshot()
    builder.add(scan_jar(mods / "beta-2.0.jar", None))

    assert snapshot.modids == ["alpha"]
    assert snapshot.entry_count == 6
    assert builder.snapshot().modids == ["alpha", "beta"]


def test_prebuilt_index_overrides_icons_without_resolution(tmp_path, monkeypatch):
    from app.icon_index import IconIndex

    mods = pack(tmp_path)
    monkeypatch.setattr(
        scan_operation,
        "_load_prebuilt_index",
        lambda _root: IconIndex(
            {"item.alpha.sword": "X"},
            by_jar={
                "alpha-1.0.jar": {"item.alpha.sword": "X"},
                "beta-2.0.jar": {},
            },
        ),
    )
    calls = []
    monkeypatch.setattr(
        "app.icon_jar_scan.resolve_icon", lambda *a, **k: calls.append(1)
    )

    store = scan_jar_directory(make_view(mods), 0)

    assert calls == []  # 有索引時完全不解析
    icons_found = {e.key: getattr(e, "icon_path", None) for e in entries_of(store)}
    assert icons_found["item.alpha.sword"] == "X"
    assert icons_found["block.alpha.stone"] is None


def test_prebuilt_index_keeps_same_key_icons_separate_per_jar(tmp_path, monkeypatch):
    from tests.test_icon_jar_scan import PNG, make_jar

    mods = tmp_path / "mods"
    shared_lang = json.dumps({"item.shared.sword": "Sword"})
    for jar_name, texture_name in (("a.jar", "sword_a"), ("b.jar", "sword_b")):
        make_jar(
            mods / jar_name,
            {
                "assets/shared/lang/en_us.json": shared_lang,
                "assets/shared/models/item/sword.json": json.dumps(
                    {
                        "parent": "item/generated",
                        "textures": {"layer0": f"shared:item/{texture_name}"},
                    }
                ),
                f"assets/shared/textures/item/{texture_name}.png": PNG,
            },
        )

    monkeypatch.setattr(icon_index, "get_data_root", lambda: tmp_path / "data")
    monkeypatch.setattr(
        icon_index,
        "load_config",
        lambda: {"translator": {"parallel_execution_workers": 1}},
    )
    monkeypatch.setattr(
        scan_operation,
        "load_config",
        lambda: {"translator": {"parallel_execution_workers": 1}},
    )

    built = icon_index.build_and_save_icon_index(mods)
    assert set(built.by_jar) == {"a.jar", "b.jar"}

    prebuilt_store = scan_jar_directory(make_view(mods), 0)
    prebuilt_icons = {
        (entry.source_jar, entry.key): entry.icon_path
        for entry in entries_of(prebuilt_store)
    }

    monkeypatch.setattr(scan_operation, "_load_prebuilt_index", lambda _root: None)
    direct_store = scan_jar_directory(make_view(mods), 0)
    direct_icons = {
        (entry.source_jar, entry.key): entry.icon_path
        for entry in entries_of(direct_store)
    }

    assert prebuilt_icons == direct_icons
    assert (
        prebuilt_icons[("a.jar", "item.shared.sword")]
        != prebuilt_icons[("b.jar", "item.shared.sword")]
    )

    incomplete_index = icon_index.IconIndex(
        {"item.shared.sword": "wrong"},
        by_jar={
            "a.jar": {"item.shared.sword": "wrong"},
            "b.jar": {"item.shared.sword": "wrong"},
        },
        complete_by_jar={"a.jar": False, "b.jar": True},
    )
    monkeypatch.setattr(
        scan_operation, "_load_prebuilt_index", lambda _root: incomplete_index
    )
    fallback_store = scan_jar_directory(make_view(mods), 0)
    fallback_icons = {
        (entry.source_jar, entry.key): entry.icon_path
        for entry in entries_of(fallback_store)
    }
    assert fallback_icons == direct_icons


# ---------------------------------------------------------------------------
# 逐步顯示
# ---------------------------------------------------------------------------


class RecordingPage:
    """記錄 ``run_task`` 的頁面替身；協程由測試決定何時執行。"""

    def __init__(self):
        self.scheduled = []
        self.overlay = []

    def run_task(self, coro_fn, *args):
        self.scheduled.append(coro_fn)

    def update(self, *args, **kwargs):
        pass


def partial_source(tmp_path):
    from app.icon_jar_scan import scan_jar

    builder = ScannedSource()
    builder.add(scan_jar(pack(tmp_path) / "alpha-1.0.jar", None))
    return builder.snapshot()


def test_partial_results_are_scheduled_once_and_applied_on_the_event_loop(tmp_path):
    view = make_view(tmp_path, page=RecordingPage())
    view._loading = True
    shown = []
    view._render_mod_list = lambda: shown.append(sorted(view.mods))
    source = partial_source(tmp_path)

    publish_partial(view, 0, source)
    publish_partial(view, 0, source)  # 已排程：不重複排程

    assert len(view.page.scheduled) == 1
    assert shown == []  # 排程時不直接碰畫面
    asyncio.run(view.page.scheduled[0]())
    assert shown == [["alpha"]]
    assert view._scan_partial is True
    assert isinstance(view.mods, ModEntryStore)


def test_partial_results_do_not_disturb_detail_search_or_finished_loads(tmp_path):
    view = make_view(tmp_path, page=RecordingPage())
    shown = []
    view._render_mod_list = lambda: shown.append(1)
    source = partial_source(tmp_path)
    view._partial_source = source

    view._loading = True
    view.current_modid = "alpha"  # 使用者正在看詳情
    apply_partial(view, 0)
    view.current_modid = None
    view._mod_search_text = "al"  # 使用者正在搜尋
    apply_partial(view, 0)
    view._mod_search_text = ""
    view._loading = False  # 載入已結束
    apply_partial(view, 0)
    view._loading = True
    apply_partial(view, 5)  # 世代已過期

    assert shown == []
    assert view._scan_partial is False


def test_partial_rows_show_counts_without_untranslated_numbers(tmp_path):
    view = make_view(tmp_path, page=RecordingPage())
    view._loading = True
    view._partial_source = partial_source(tmp_path)

    apply_partial(view, 0)

    row = view._mod_row_pool[0]
    assert row.modid == "alpha"
    assert "總數 6" in row._counts.value
    assert "掃描中" in row._chip_text.value


def test_finish_load_with_store_replaces_partial_and_keeps_open_detail(tmp_path):
    mods = pack(tmp_path)
    view = make_view(mods, page=RecordingPage())
    store = scan_jar_directory(view, 0)
    view._scan_partial = True
    view.mods = ModEntryStore(partial_source(tmp_path), {})
    rendered = []
    view._render_mod_list = lambda: rendered.append(1)
    view.current_modid = "alpha"  # 掃描期間使用者開了詳情

    view._finish_load(store, "jar_directory")

    assert view.mods is store
    assert view._scan_partial is False
    assert view._source_cache is store.source
    assert view._cache_meta["source_identity"] == store.source.identity
    assert rendered == []  # 不把使用者從詳情拉回清單

    view.current_modid = None
    view._finish_load(store, "jar_directory")
    assert rendered == [1]


def test_finish_load_discards_partial_when_scan_found_nothing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    view = make_view(empty, page=RecordingPage())
    view._scan_partial = True
    view.mods = ModEntryStore(partial_source(tmp_path), {})
    view._render_mod_list = lambda: None

    view._finish_load(ModEntryStore(ScannedSource(), {}), "jar_directory")

    assert len(view.mods) == 0
    assert view._scan_partial is False


# ---------------------------------------------------------------------------
# 不在 UI 執行緒
# ---------------------------------------------------------------------------


def test_cold_scan_runs_off_the_event_loop_thread(tmp_path, monkeypatch):
    from app.views.icon_preview.load_operation import load_icon_preview

    mods = pack(tmp_path)
    view = make_view(mods)
    view.review_root = tmp_path
    seen = {}
    real = scan_operation.scan_jar_directory

    def spy(v, generation=None):
        seen["scan"] = threading.current_thread()
        return real(v, generation)

    monkeypatch.setattr("app.views.icon_preview_view.scan_jar_directory", spy)
    monkeypatch.setattr(
        view, "_finish_load", lambda entries, mode: seen.update(done=len(entries))
    )

    async def run():
        seen["loop"] = threading.current_thread()
        await load_icon_preview(view, view._load_generation)

    asyncio.run(run())

    assert seen["scan"] is not seen["loop"]
    assert seen["done"] == 2


def test_icon_resolution_runs_in_worker_threads_not_the_caller(tmp_path, monkeypatch):
    """執行緒池 worker 的解析不經過呼叫端（event loop）執行緒。"""
    from app import icon_jar_scan
    from app.icon_scan_pool import stream_jar_scans

    mods = pack(tmp_path)
    main = threading.current_thread()
    seen = []
    real = icon_jar_scan.resolve_icon

    def spy(*args, **kwargs):
        seen.append(threading.current_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(icon_jar_scan, "resolve_icon", spy)
    scans = list(stream_jar_scans(mods, sorted(mods.glob("*.jar")), workers=1))

    assert len(scans) == 2 and seen
    assert all(thread is not main for thread in seen)
