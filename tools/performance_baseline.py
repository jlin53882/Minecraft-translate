"""可重現的離線效能基準：UI 建構、JAR 掃描與批次選取。"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from datetime import datetime
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def summarize_samples(samples: list[float]) -> dict[str, float]:
    """回傳毫秒樣本的穩定統計摘要。"""
    if not samples:
        raise ValueError("samples must not be empty")
    return {
        "median_ms": round(statistics.median(samples), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "stdev_ms": round(statistics.pstdev(samples), 3),
    }


def benchmark(
    operation: Callable[[], object], repeats: int
) -> dict[str, float | int | list[float]]:
    """以 perf_counter 重複量測操作，保留原始樣本供後續變異檢查。"""
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1000)
    return {
        **summarize_samples(samples),
        "repeats": repeats,
        "samples_ms": [round(sample, 3) for sample in samples],
    }


def measure_dashboard_workloads(repeats: int) -> dict[str, dict]:
    """分開量測工作台資料來源與 Python 控制項更新，不啟動真實服務。"""
    from app.services_impl import config_service
    from app.shell.task_manager import (
        STATUS_DONE,
        STATUS_ERROR,
        STATUS_RUNNING,
        TaskInfo,
    )
    from app.views.dashboard.dashboard_data import build_dashboard_data
    from app.views.dashboard_view import DashboardView
    from translation_tool.utils.cache_overview import build_cache_overview

    with tempfile.TemporaryDirectory(prefix="minecraft-dashboard-perf-") as temp_name:
        root = Path(temp_name)
        rules_path = root / "replace_rules.json"
        rules_path.write_text(
            json.dumps(
                [
                    {"from": f"source-{index}", "to": f"target-{index}"}
                    for index in range(1_000)
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        original_rules_path = config_service.REPLACE_RULES_PATH
        config_service.REPLACE_RULES_PATH = str(rules_path)
        try:
            rules_read = benchmark(config_service.load_replace_rules, repeats)
        finally:
            config_service.REPLACE_RULES_PATH = original_rules_path

        cache_root = root / "cache"
        cache_types = ["lang", "patchouli", "ftbquests", "kubejs", "md"]
        translation_cache = {}
        cache_file_path = {}
        shard_paths = {}
        for cache_type in cache_types:
            type_dir = cache_root / cache_type
            type_dir.mkdir(parents=True)
            cache_file_path[cache_type] = type_dir / "cache.json"
            active_shard = type_dir / "active.json"
            shard_paths[cache_type] = active_shard
            (type_dir / "active_shard.json").write_text("active", encoding="utf-8")
            entries = {
                f"fixture.{cache_type}.{index}": {
                    "src": f"Source {index}",
                    "dst": f"譯文 {index}",
                }
                for index in range(1_000)
            }
            active_shard.write_text(
                json.dumps(entries, ensure_ascii=False), encoding="utf-8"
            )
            translation_cache[cache_type] = entries

        overview_operation = lambda: build_cache_overview(
            cache_types=cache_types,
            translation_cache=translation_cache,
            is_dirty=dict.fromkeys(cache_types, False),
            session_new_entries={cache_type: {} for cache_type in cache_types},
            cache_file_path=cache_file_path,
            rolling_shard_size=10_000,
            active_shard_file="active_shard.json",
            get_active_shard_path=shard_paths.__getitem__,
            load_config=lambda: {"translator": {"cache_directory": str(cache_root)}},
            cache_dir_name="cache",
            resolve_project_path=lambda value: Path(value),
        )
        cache_overview_read = benchmark(overview_operation, repeats)

        tasks = [
            TaskInfo(
                id=index,
                name=f"固定任務 {index}",
                view_key="pipeline",
                status=(STATUS_RUNNING, STATUS_DONE, STATUS_ERROR)[index % 3],
                progress=(index % 100) / 100,
                started_at=float(index),
            )
            for index in range(300)
        ]
        dashboard_data = build_dashboard_data(
            cache_overview=overview_operation(),
            rules_count=1_000,
            key_snapshot=[],
            active=[task for task in tasks if task.status == STATUS_RUNNING],
            recent=[task for task in tasks if task.status != STATUS_RUNNING],
        )
        dashboard_view = DashboardView(
            page=None,
            cache_overview_loader=overview_operation,
            rules_count_loader=lambda: 1_000,
            key_snapshot_loader=list,
        )
        task_render = benchmark(
            lambda: dashboard_view.refresh_view(dashboard_data), repeats
        )

    return {
        "cache_overview_read": cache_overview_read,
        "replace_rules_read": rules_read,
        "dashboard_task_render": task_render,
    }


def build_synthetic_jars(root: Path, count: int) -> list[Path]:
    """建立確定性的 JAR 測試資料，包含巢狀路徑。"""
    jars = []
    for index in range(count):
        parent = root / ("nested" if index % 10 == 0 else "top")
        parent.mkdir(parents=True, exist_ok=True)
        jar_path = parent / f"fixture-{index:04d}.jar"
        payload = json.dumps({"fixture.key": f"Value {index}"}, ensure_ascii=False)
        with zipfile.ZipFile(
            jar_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr("assets/fixture/lang/en_us.json", payload)
            archive.writestr("META-INF/mods.toml", "modLoader='javafml'\n")
        jars.append(jar_path)
    return jars


def measure_jar_scans(repeats: int) -> list[dict]:
    """量測以明確清單掃描 JAR 的耗時，不使用網路。"""
    from translation_tool.utils.jar_browser import scan_jars

    results = []
    pattern = r"assets/fixture/lang/en_us\.json$"
    with tempfile.TemporaryDirectory(prefix="minecraft-perf-jars-") as temp_name:
        root = Path(temp_name)
        for count in (10, 100):
            jars = build_synthetic_jars(root / str(count), count)
            samples = []
            for _ in range(repeats):
                started = time.perf_counter()
                scanned = scan_jars(root, [pattern], max_workers=4, jar_files=jars)
                elapsed = time.perf_counter() - started
                if len(scanned) != count:
                    raise RuntimeError(
                        f"JAR fixture scan incomplete: {len(scanned)} != {count}"
                    )
                samples.append(elapsed * 1000)
            summary = summarize_samples(samples)
            seconds = summary["median_ms"] / 1000
            results.append(
                {
                    "jar_count": count,
                    "repeats": repeats,
                    **summary,
                    "samples_ms": [round(sample, 3) for sample in samples],
                    "jars_per_second": round(count / seconds, 3) if seconds else None,
                }
            )
    return results


def _batch_items(count: int = 10_000) -> list[dict[str, str]]:
    return [
        {
            "id": str(index),
            "text": f"合成測試文字 {index} with ASCII payload and symbols §{{value}}",
        }
        for index in range(count)
    ]


def measure_batch_selection(repeats: int) -> list[dict]:
    """比較本地 token 預算選取與僅依數量選取的耗時。"""
    from translation_tool.core.lm_batch_budget import reset_trackers, select_batch_size

    items = _batch_items()
    cases = [
        ("count_only", {"token_budget_enabled": False}),
        (
            "token_budget",
            {
                "token_budget_enabled": True,
                "max_output_token_budget": 24_000,
                "max_input_token_budget": 60_000,
                "output_token_factor": 1.5,
            },
        ),
    ]
    results = []
    for name, config in cases:
        reset_trackers()

        def operation(config=config):
            selected = select_batch_size(items, "lang", 300, config)
            if not 1 <= selected <= 300:
                raise RuntimeError(f"invalid selected batch size: {selected}")

        summary = benchmark(operation, repeats)
        results.append(
            {
                "mode": name,
                "item_count": len(items),
                "count_cap": 300,
                **summary,
                "operations_per_second": round(1000 / summary["median_ms"], 3)
                if summary["median_ms"]
                else None,
            }
        )
    return results


def measure_list_construction(repeats: int) -> list[dict]:
    """量測大型 Flet 清單控制項在 Python 端的建構成本。"""
    import flet as ft

    results = []
    for count in (1_000, 5_000, 10_000):

        def operation(count=count):
            controls = [ft.Text(f"固定列 {index}") for index in range(count)]
            view = ft.ListView(controls=controls, item_extent=28)
            if len(view.controls) != count:
                raise RuntimeError("list fixture construction incomplete")

        results.append({"row_count": count, **benchmark(operation, repeats)})
    return results


def summarize_ui_report(path: Path) -> dict:
    """分開整理首次頁面建構與同一 session 回訪的耗時。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    view_cases = [case for case in cases if case.get("kind") == "view"]
    revisit_cases = [case for case in cases if case.get("kind") == "view_revisit"]
    if not view_cases or not revisit_cases:
        raise ValueError(
            f"UI report is missing first-build or warm-revisit cases: {path}"
        )
    startup = [float(case["startup_ms"]) for case in view_cases]
    browser_ready = [float(case["browser_ready_ms"]) for case in view_cases]
    by_view: dict[str, list[float]] = {}
    for case in view_cases:
        by_view.setdefault(case["view"], []).append(float(case["server_build_ms"]))
    revisit_by_view: dict[str, list[float]] = {}
    for case in revisit_cases:
        revisit_by_view.setdefault(case["view"], []).append(float(case["navigate_ms"]))
    return {
        "source": str(path),
        "startup": summarize_samples(startup),
        "browser_ready": summarize_samples(browser_ready),
        "first_view_build": {
            key: summarize_samples(samples) for key, samples in sorted(by_view.items())
        },
        "warm_view_revisit": {
            key: summarize_samples(samples)
            for key, samples in sorted(revisit_by_view.items())
        },
    }


def environment_info() -> dict[str, str | bool | None]:
    """回傳重現基準所需的環境欄位。"""
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "flet": metadata.version("flet"),
        "playwright": metadata.version("playwright"),
        "git_commit": _git_commit(),
        "git_worktree_dirty": _git_dirty(),
    }


def _git_commit() -> str:
    """取得本次量測工作目錄的 commit，失敗時保留明確未知值。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def _git_dirty() -> bool | None:
    """確認效能樣本是否來自含未提交變更的工作樹。"""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return bool(result.stdout.strip())


def render_markdown(report: dict) -> str:
    """產生簡潔、可供人閱讀的基準摘要。"""
    lines = [
        "# Performance baseline result",
        "",
        f"- Generated: `{report['environment']['generated_at']}`",
        f"- Platform: `{report['environment']['platform']}`",
        f"- Python: `{report['environment']['python']}`",
        f"- Flet: `{report['environment']['flet']}`",
        f"- Git commit: `{report['environment']['git_commit']}`",
        f"- Git worktree dirty: `{report['environment']['git_worktree_dirty']}`",
        "",
    ]
    ui = report.get("ui")
    if ui:
        lines.extend(
            [
                "## UI",
                "",
                f"- AppShell mount median: **{ui['startup']['median_ms']} ms**",
                f"- Browser ready median: **{ui['browser_ready']['median_ms']} ms**",
                "",
                "### First view build",
                "",
            ]
        )
        for key, values in ui["first_view_build"].items():
            lines.append(f"- `{key}`: {values['median_ms']} ms")
        lines.extend(["", "### Warm view revisit", ""])
        for key, values in ui["warm_view_revisit"].items():
            lines.append(f"- `{key}`: {values['median_ms']} ms")
            lines.append("")
    lines.extend(["## JAR scan", ""])
    for item in report["jar_scan"]:
        lines.append(
            f"- {item['jar_count']} JAR: {item['median_ms']} ms "
            f"({item['jars_per_second']} JAR/s)"
        )
    lines.extend(["", "## LM batch selection", ""])
    for item in report["batch_selection"]:
        lines.append(
            f"- `{item['mode']}`: {item['median_ms']} ms "
            f"({item['operations_per_second']} ops/s)"
        )
    lines.extend(["", "## Large list construction", ""])
    for item in report["list_construction"]:
        lines.append(f"- {item['row_count']} rows: {item['median_ms']} ms")
    lines.extend(["", "## Dashboard", ""])
    for metric, samples in report["dashboard"].items():
        lines.append(
            f"- `{metric}`: {samples['median_ms']} ms "
            f"(range {samples['min_ms']}–{samples['max_ms']} ms, "
            f"σ {samples['stdev_ms']} ms)"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "These values are a same-platform baseline, not a cross-platform score. ",
            "A regression budget must be proposed only after multiple runs establish normal variance.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    """命令列進入點。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ui-report", type=Path)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.repeats < 3:
        parser.error("--repeats must be at least 3")

    report = {
        "environment": environment_info(),
        "ui": summarize_ui_report(args.ui_report) if args.ui_report else None,
        "jar_scan": measure_jar_scans(args.repeats),
        "batch_selection": measure_batch_selection(max(100, args.repeats * 20)),
        "list_construction": measure_list_construction(args.repeats),
        "dashboard": measure_dashboard_workloads(args.repeats),
        "network_used": False,
        "real_api_used": False,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "performance.json"
    markdown_path = args.output_dir / "performance.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
