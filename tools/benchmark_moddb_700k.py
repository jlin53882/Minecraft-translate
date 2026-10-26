"""Mod DB 大型資料庫（70 萬筆以上）讀取效能基準。

每個案例在全新的 Python 行程中量測：第 1 次是冷（首次）呼叫，之後重複 ``--repeats``
次取熱的中位數與 p95。與 UI 的 worker 相同，每次迭代都開一條新的唯讀連線並包在讀取交易內
（``moddb_read_snapshot``），所以 SQLite 的連線頁面快取不會跨迭代共用；只有程序內的
``READ_CACHE`` 與品質翻頁快取會跨迭代。連線開啟時間不計入。

只讀取資料庫，不會寫入。執行範例（需先備妥大型資料庫，例如以 SQLite backup 複製的檔案）::

    uv run python -m tools.benchmark_moddb_700k --db path/to/moddb.db \
        --output .artifacts/moddb-700k/read.json
    uv run python -m tools.benchmark_moddb_700k --db path/to/moddb.db --only list_

數字取決於機器與 SQLite 版本，是診斷證據，不是 CI 門檻。
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from translation_tool.translation_db import (
    EntryFilter,
    QualityFilter,
    TimeFilter,
    TranslationDB,
)
from translation_tool.translation_db.read_cache import READ_CACHE

PAGE_SIZE = 50
DEFAULT_REPEATS = 5


@dataclass
class Ctx:
    """案例使用的真實資料特徵（用一條獨立的原生連線取得，不經過 TranslationDB）。"""

    version: str
    versions: list[str]
    big_mod: str
    mid_mod: str
    small_mod: str
    entry_id: int
    deep_entry_id: int
    manual_entry_id: int
    path: Path


def _discover(path: Path) -> Ctx:
    import sqlite3

    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        versions = [r[0] for r in conn.execute("SELECT DISTINCT mc_version FROM entry")]
        version = "1.20.1" if "1.20.1" in versions else min(versions)
        mods = conn.execute(
            "SELECT mod_id, COUNT(*) n FROM entry WHERE mc_version=? "
            "GROUP BY mod_id ORDER BY n DESC",
            (version,),
        ).fetchall()
        small = next((m for m, n in reversed(mods) if n >= 8), mods[-1][0])
        entry_id = conn.execute(
            "SELECT id FROM entry WHERE mc_version=? AND mod_id=? "
            "ORDER BY kind,key LIMIT 1 OFFSET 20",
            (version, mods[len(mods) // 4][0]),
        ).fetchone()[0]
        deep = conn.execute(
            "SELECT id FROM entry WHERE mc_version=? ORDER BY mod_id,kind,key,id "
            "LIMIT 1 OFFSET 150000",
            (version,),
        ).fetchone()[0]
        manual = conn.execute(
            "SELECT e.id FROM translation t JOIN entry e ON e.id=t.entry_id "
            "WHERE t.source=6 AND e.mc_version=? LIMIT 1",
            (version,),
        ).fetchone()
        return Ctx(
            version=version,
            versions=sorted(versions),
            big_mod=mods[0][0],
            mid_mod=mods[min(len(mods) - 1, 12)][0],
            small_mod=small,
            entry_id=entry_id,
            deep_entry_id=deep,
            manual_entry_id=manual[0] if manual else entry_id,
            path=path,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------- 案例定義
Case = Callable[[TranslationDB, Ctx], Any]


def _crit(ctx: Ctx, **kw) -> EntryFilter:
    kw.setdefault("version", ctx.version)
    return EntryFilter(**kw)


def _page(ctx: Ctx, db: TranslationDB, criteria: EntryFilter, where: str):
    """第一頁、中間頁、最後一頁（依該條件的實際總數換算偏移）。"""
    if where == "first":
        offset = 0
    else:
        total = _TOTALS.get(_criteria_key(criteria))
        if total is None:
            total = db.list_entries(criteria=criteria, limit=1)[1]
        last = max(0, (total - 1) // PAGE_SIZE * PAGE_SIZE)
        offset = last if where == "last" else (total // 2) // PAGE_SIZE * PAGE_SIZE
    return db.list_entries(criteria=criteria, limit=PAGE_SIZE, offset=offset)


_TOTALS: dict[str, int] = {}


def _criteria_key(criteria: EntryFilter) -> str:
    return repr(criteria)


def _list_case(**kw) -> Callable[[str], Case]:
    def make(where: str) -> Case:
        def run(db: TranslationDB, ctx: Ctx):
            return _page(ctx, db, _crit(ctx, **_resolve(kw, ctx)), where)

        return run

    return make


def _resolve(kw: dict, ctx: Ctx) -> dict:
    out = {}
    for key, value in kw.items():
        if value == "$big":
            value = ctx.big_mod
        elif value == "$mid":
            value = ctx.mid_mod
        elif value == "$small":
            value = ctx.small_mod
        out[key] = value
    return out


LIST_FILTERS: dict[str, dict] = {
    "all": {},
    "all_desc": {"sort_by": "default_desc"},
    "state_none": {"state": "none"},
    "state_diff": {"state": "diff"},
    "state_manual": {"state": "manual"},
    "state_ok": {"state": "ok"},
    "state_same": {"state": "same"},
    "state_changed": {"state": "changed"},
    "review_unreviewed": {"review_status": "unreviewed"},
    "review_reviewed": {"review_status": "reviewed"},
    "review_legacy": {"review_status": "legacy_unknown"},
    "source_ai": {"source": 0},
    "source_jar_tw": {"source": 1},
    "source_s2t": {"source": 2},
    "source_manual": {"source": 6},
    "mod_big": {"mod_id": "$big"},
    "mod_small": {"mod_id": "$small"},
    "kind_lang": {"kind": "lang"},
    "kind_patchouli": {"kind": "patchouli"},
    "kind_guide": {"kind": "guide"},
    "mod_kind_state": {"mod_id": "$mid", "kind": "lang", "state": "diff"},
    "q_en_all": {"query": "block"},
    "q_en_common": {"query": "the"},
    "q_en_rare": {"query": "xyzzyplugh"},
    "q_zh_all": {"query": "方塊"},
    "q_zh_common": {"query": "的"},
    "q_source": {"query": "block", "query_field": "source"},
    "q_translation": {"query": "方塊", "query_field": "translation"},
    "q_key": {"query": "block.", "query_field": "key"},
    "q_mod_en": {"query": "block", "mod_id": "$big"},
    "sort_entry_newest": {"sort_by": "entry_newest"},
    "sort_entry_oldest": {"sort_by": "entry_oldest"},
    "sort_eff_newest": {"sort_by": "effective_updated_newest"},
    "sort_eff_oldest": {"sort_by": "effective_updated_oldest"},
    "sort_act_newest": {"sort_by": "manual_activity_newest"},
    "sort_act_oldest": {"sort_by": "manual_activity_oldest"},
    "quality_mismatch": {"quality": QualityFilter(status="mismatch")},
    "quality_mismatch_missing": {
        "quality": QualityFilter(status="mismatch", direction="missing")
    },
    "quality_mismatch_ph": {
        "quality": QualityFilter(status="mismatch", token_category="placeholder")
    },
    "quality_consistent": {"quality": QualityFilter(status="consistent")},
    "quality_unknown_source": {"quality": QualityFilter(status="unknown_source")},
    "quality_missing": {"quality": QualityFilter(status="missing_translation")},
    "quality_whitespace": {"quality": QualityFilter(status="whitespace")},
    "quality_species_name": {"quality": QualityFilter(status="species_name")},
    "quality_species_format": {"quality": QualityFilter(status="species_format")},
    "quality_mismatch_mod": {
        "quality": QualityFilter(status="mismatch"),
        "mod_id": "$big",
    },
    "quality_mismatch_sort": {
        "quality": QualityFilter(status="mismatch"),
        "sort_by": "entry_newest",
    },
    "time_eff_updated": {
        "time": TimeFilter(kind="effective_updated", start_utc="2000-01-01 00:00:00")
    },
    "time_entry_created": {
        "time": TimeFilter(kind="entry_created", start_utc="2000-01-01 00:00:00")
    },
    "time_manual_activity": {"time": TimeFilter(kind="manual_activity")},
}


def _build_cases() -> dict[str, Case]:
    cases: dict[str, Case] = {}
    for name, kw in LIST_FILTERS.items():
        make = _list_case(**kw)
        wheres = ("first", "mid", "last") if name in _PAGED else ("first",)
        for where in wheres:
            cases[f"list_{name}:{where}"] = make(where)
    cases.update(_stat_cases())
    cases.update(_detail_cases())
    cases.update(_candidate_cases())
    cases.update(_scope_cases())
    return cases


_PAGED = {
    "all",
    "all_desc",
    "state_none",
    "state_diff",
    "state_ok",
    "mod_big",
    "q_en_all",
    "sort_entry_newest",
    "sort_eff_newest",
    "sort_act_newest",
    "quality_mismatch",
    "quality_consistent",
    "quality_missing",
}


def _stat_cases() -> dict[str, Case]:
    def src_stats(db, ctx):
        return db.version_stats_from_effective_sources(
            db.effective_source_stats_by_version()
        )

    return {
        "stat_versions": lambda db, ctx: db.versions(),
        "stat_mods": lambda db, ctx: db.mods(ctx.version),
        "stat_kinds": lambda db, ctx: db.kinds(ctx.version),
        "stat_version_stats": lambda db, ctx: db.version_stats(),
        "stat_effective_source_stats": lambda db, ctx: (
            db.effective_source_stats_by_version()
        ),
        "stat_version_stats_from_sources": src_stats,
        "stat_effective_source_codes": lambda db, ctx: db.effective_source_codes(
            ctx.version
        ),
        "stat_overview": lambda db, ctx: db.overview(),
        "stat_missing_by_mod": lambda db, ctx: db.missing_by_mod(ctx.version),
        "stat_last_scans": lambda db, ctx: db.last_scans(),
        "stat_src_changes": lambda db, ctx: db.src_changes(ctx.version),
        "stat_count_entries": lambda db, ctx: db.count_entries(),
        "stat_filter_options_mod": lambda db, ctx: db.filter_option_values(
            _crit(ctx, mod_id=ctx.big_mod)
        ),
        "stat_filter_options_query": lambda db, ctx: db.filter_option_values(
            _crit(ctx, query="block")
        ),
        "stat_filter_options_quality": lambda db, ctx: db.filter_option_values(
            _crit(ctx, quality=QualityFilter(status="mismatch"))
        ),
        "stat_filter_options_state": lambda db, ctx: db.filter_option_values(
            _crit(ctx, state="diff")
        ),
        "stat_time_range_eff": lambda db, ctx: db.time_data_range(
            _crit(
                ctx, time=TimeFilter(kind="effective_updated", start_utc="2099-01-01")
            )
        ),
        "stat_time_range_activity": lambda db, ctx: db.time_data_range(
            _crit(ctx, time=TimeFilter(kind="manual_activity", start_utc="2099-01-01"))
        ),
        "stat_time_range_entry": lambda db, ctx: db.time_data_range(
            _crit(ctx, time=TimeFilter(kind="entry_created", start_utc="2099-01-01"))
        ),
    }


def _preview_manual_review(db: TranslationDB, ctx: Ctx):
    entry = db.get_entry(ctx.manual_entry_id)
    return db.preview_manual_review(
        ctx.manual_entry_id,
        expected_zh_tw=entry.zh_tw or "",
        expected_source=entry.source,
        expected_review_status=entry.review_status,
        expected_checker=entry.checker,
    )


def _detail_cases() -> dict[str, Case]:
    return {
        "detail_entry_detail": lambda db, ctx: db.entry_detail(ctx.entry_id),
        "detail_entry_detail_deep": lambda db, ctx: db.entry_detail(ctx.deep_entry_id),
        "detail_get_entry": lambda db, ctx: db.get_entry(ctx.entry_id),
        "detail_marks": lambda db, ctx: db.smart_review_marks(
            list(range(ctx.entry_id, ctx.entry_id + PAGE_SIZE))
        ),
        "detail_preview_manual": lambda db, ctx: db.preview_manual(
            ctx.entry_id, "測試譯文"
        ),
        "detail_preview_manual_review": _preview_manual_review,
        "detail_load_mod": lambda db, ctx: db.load_mod(ctx.big_mod),
        "detail_reviewed_glossary": lambda db, ctx: db.reviewed_lang_glossary(
            ctx.big_mod, ctx.version, ["Iron Ingot", "Stone Block"]
        ),
    }


def _candidate_cases() -> dict[str, Case]:
    return {
        "cand_count_untranslated": lambda db, ctx: db.count_untranslated(ctx.version),
        "cand_count_untranslated_mod": lambda db, ctx: db.count_untranslated(
            ctx.version, [ctx.big_mod]
        ),
        "cand_count_reusable": lambda db, ctx: db.count_reusable(ctx.version),
        "cand_count_reusable_mod": lambda db, ctx: db.count_reusable(
            ctx.version, [ctx.big_mod]
        ),
        "cand_untranslated_entries": lambda db, ctx: db.untranslated_entries(
            ctx.version
        ),
        "cand_count_same_as_source_ai": lambda db, ctx: db.count_same_as_source_ai(
            ctx.version
        ),
        "cand_same_as_source_ai": lambda db, ctx: db.same_as_source_ai_entries(
            ctx.version
        ),
        "cand_mismatched": lambda db, ctx: db.mismatched_translation_entries(
            ctx.version
        ),
        "cand_smart_review_40": lambda db, ctx: db.smart_review_candidates(
            version=ctx.version, limit=40
        ),
        "cand_smart_review_1000": lambda db, ctx: db.smart_review_candidates(
            version=ctx.version, limit=1000
        ),
        "cand_smart_review_count": lambda db, ctx: db.count_smart_review_candidates(
            version=ctx.version
        ),
        "cand_bulk_review_preview": lambda db, ctx: db.preview_bulk_review(
            ctx.version, 0, mod_id=ctx.mid_mod
        ),
        "cand_bulk_review_preview_all": lambda db, ctx: db.preview_bulk_review(
            ctx.version, None
        ),
        "cand_batch_replace_preview_small": lambda db, ctx: db.preview_batch_replace(
            _crit(ctx, mod_id=ctx.small_mod), "a", "b"
        ),
    }


def _scope_cases() -> dict[str, Case]:
    return {
        "scope_smart_review_options": lambda db, ctx: db.smart_review_scope_options(
            version=ctx.version
        ),
        "scope_bulk_review_options": lambda db, ctx: db.bulk_review_scope_options(
            ctx.version
        ),
        "scope_cleanup_candidates": lambda db, ctx: db.cleanup_candidates(ctx.version),
        "scope_cleanup_batches": lambda db, ctx: db.cleanup_batches(),
    }


def _ui_snapshot(db: TranslationDB, ctx: Ctx, criteria: EntryFilter, page: int = 1):
    """與 ``panel_refresh._load_entries_snapshot`` 相同的呼叫組合。"""
    db.versions()
    db.mods(criteria.version)
    db.kinds(criteria.version)
    db.effective_source_codes(criteria.version)
    filtered = bool(
        criteria.mod_id
        or criteria.kind
        or criteria.source is not None
        or criteria.state != "all"
        or criteria.query.strip()
        or criteria.review_status
        or criteria.time
        or criteria.quality.active
    )
    if filtered:
        db.filter_option_values(criteria)
    rows, total = db.list_entries(
        criteria=criteria, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    detail = db.entry_detail(rows[0].id) if rows else None
    db.smart_review_marks(sorted(r.id for r in rows))
    return rows, total, detail


UI_SCENARIOS: dict[str, dict] = {
    "all": {},
    "mod": {"mod_id": "$big"},
    "state_diff": {"state": "diff"},
    "state_none": {"state": "none"},
    "review": {"review_status": "unreviewed"},
    "source_ai": {"source": 0},
    "search_en": {"query": "block"},
    "search_zh": {"query": "方塊"},
    "quality_mismatch": {"quality": QualityFilter(status="mismatch")},
    "quality_missing": {"quality": QualityFilter(status="missing_translation")},
    "sort_newest": {"sort_by": "entry_newest"},
}


def _ui_case(kw: dict) -> Case:
    return lambda db, ctx: _ui_snapshot(db, ctx, _crit(ctx, **_resolve(kw, ctx)))


def all_cases() -> dict[str, Case]:
    cases = _build_cases()
    for name, kw in UI_SCENARIOS.items():
        cases[f"ui_snapshot_{name}"] = _ui_case(kw)
    return cases


# ---------------------------------------------------------------- 量測
def _open(ctx: Ctx) -> TranslationDB:
    db = TranslationDB(ctx.path, readonly=True, create=False)
    db._conn.execute("BEGIN")
    return db


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95 + 0.999999) - 1)]


def run_case(path: Path, name: str, repeats: int) -> dict:
    """在目前（全新）行程中量測一個案例。"""
    ctx = _discover(path)
    case = all_cases()[name]
    # 需要總數換算最後頁／中間頁時，用另一個 TranslationDB 預先取得，再清掉程序內快取
    if name.endswith((":mid", ":last")):
        pre = _open(ctx)
        _prime_total(pre, ctx, name)
        pre.close()
        _clear_process_caches()
    samples = _timed_runs(ctx, case, repeats, clear_caches=False)
    result_size = samples.pop("result")
    # 未命中程序內快取（新的篩選組合或資料寫入後）：快取清空，SQLite 檔案頁仍在 OS 快取
    recompute = _timed_runs(ctx, case, repeats, clear_caches=True)
    recompute.pop("result")
    hot = samples["times"][1:]
    miss = recompute["times"][1:]
    return {
        "case": name,
        "cold_ms": round(samples["times"][0], 2),
        "hot_median_ms": round(statistics.median(hot), 2),
        "hot_p95_ms": round(_p95(hot), 2),
        "miss_median_ms": round(statistics.median(miss), 2),
        "miss_p95_ms": round(_p95(miss), 2),
        "samples_ms": [round(s, 2) for s in samples["times"]],
        "miss_samples_ms": [round(s, 2) for s in recompute["times"]],
        "result": result_size,
    }


def _clear_process_caches() -> None:
    READ_CACHE._values.clear()
    READ_CACHE._bytes = 0
    TranslationDB._quality_page_cache = None


def _timed_runs(ctx: Ctx, case: Case, repeats: int, *, clear_caches: bool) -> dict:
    times: list[float] = []
    result_size = None
    for _ in range(1 + repeats):
        if clear_caches:
            _clear_process_caches()
        db = _open(ctx)
        try:
            started = time.perf_counter()
            result = case(db, ctx)
            times.append((time.perf_counter() - started) * 1000)
        finally:
            db._conn.rollback()
            db.close()
        if result_size is None:
            result_size = _size(result)
    return {"times": times, "result": result_size}


def _prime_total(db: TranslationDB, ctx: Ctx, name: str) -> None:
    base = name.split(":")[0].removeprefix("list_")
    criteria = _crit(ctx, **_resolve(LIST_FILTERS[base], ctx))
    total = db.list_entries(criteria=criteria, limit=1)[1]
    _TOTALS[_criteria_key(criteria)] = total


def _size(result: Any) -> Any:
    if isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], list):
        return {"rows": len(result[0]), "total": result[1]}
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], list):
        return {"rows": len(result[0]), "total": result[1]}
    if isinstance(result, (list, tuple, dict, set)):
        return {"len": len(result)}
    return None if result is None else str(type(result).__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--only", default="", help="案例名稱包含此字串才執行")
    parser.add_argument("--case", default="", help="內部使用：在本行程量測單一案例")
    parser.add_argument("--list", action="store_true", help="只列出案例名稱")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.case:
        print(
            json.dumps(run_case(args.db, args.case, args.repeats), ensure_ascii=False)
        )
        return 0
    names = [n for n in all_cases() if args.only in n]
    if args.list:
        print("\n".join(names))
        return 0
    results = []
    for name in names:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.benchmark_moddb_700k",
                "--db",
                str(args.db),
                "--repeats",
                str(args.repeats),
                "--case",
                name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            row = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            row = {"case": name, "error": (proc.stderr or proc.stdout)[-400:]}
        results.append(row)
        print(
            f"{name:42s} cold={row.get('cold_ms', '-'):>8} "
            f"hot={row.get('hot_median_ms', '-'):>8}/{row.get('hot_p95_ms', '-'):>8} "
            f"miss={row.get('miss_median_ms', '-'):>8}/{row.get('miss_p95_ms', '-'):>8} "
            f"{row.get('result') or row.get('error', '')}",
            flush=True,
        )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
