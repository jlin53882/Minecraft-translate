"""效能基準輸出與 Dashboard workload 的契約測試。"""

from __future__ import annotations

import json

import pytest

from tools.performance_baseline import (
    benchmark,
    measure_dashboard_workloads,
    measure_jar_scans,
    summarize_samples,
    summarize_ui_report,
)


def test_summarize_samples_reports_range_and_variation() -> None:
    """摘要須保留中位數、範圍與樣本變異。"""
    result = summarize_samples([1.0, 2.0, 3.0])

    assert result == {
        "median_ms": 2.0,
        "min_ms": 1.0,
        "max_ms": 3.0,
        "stdev_ms": 0.816,
    }


def test_summarize_samples_rejects_empty_input() -> None:
    """空樣本不得被錯誤摘要成零毫秒。"""
    with pytest.raises(ValueError, match="must not be empty"):
        summarize_samples([])


def test_benchmark_keeps_every_raw_sample() -> None:
    calls = 0

    def operation() -> None:
        """累加呼叫次數以確認量測重複次數。"""
        nonlocal calls
        calls += 1

    result = benchmark(operation, repeats=4)

    assert calls == 4
    assert result["repeats"] == 4
    assert len(result["samples_ms"]) == 4
    assert result["min_ms"] <= result["median_ms"] <= result["max_ms"]


def test_jar_scan_benchmark_keeps_each_raw_sample() -> None:
    """每種 JAR fixture 都保留全部重複量測樣本。"""
    results = measure_jar_scans(repeats=3)

    assert [item["jar_count"] for item in results] == [10, 100]
    assert all(item["repeats"] == 3 for item in results)
    assert all(len(item["samples_ms"]) == 3 for item in results)


def test_summarize_ui_report_separates_cold_build_and_warm_revisit(
    tmp_path,
) -> None:
    """UI 報告摘要分開首次 lazy build 與 warm revisit 樣本。"""
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "kind": "view",
                        "view": "dashboard",
                        "startup_ms": 10,
                        "browser_ready_ms": 20,
                        "server_build_ms": 7,
                    },
                    {
                        "kind": "view_revisit",
                        "view": "dashboard",
                        "navigate_ms": 2,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = summarize_ui_report(report_path)

    assert result["first_view_build"]["dashboard"]["median_ms"] == 7.0
    assert result["warm_view_revisit"]["dashboard"]["median_ms"] == 2.0


def test_summarize_ui_report_requires_both_first_and_revisit_samples(tmp_path) -> None:
    """缺少首次或回訪資料時，不得輸出不完整摘要。"""
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "kind": "view",
                        "view": "dashboard",
                        "startup_ms": 10,
                        "browser_ready_ms": 20,
                        "server_build_ms": 7,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing first-build or warm-revisit"):
        summarize_ui_report(report_path)


def test_dashboard_workloads_are_separate_and_reproducible(tmp_path) -> None:
    """Dashboard 指標各自輸出多次原始樣本且不留下臨時資料。"""
    results = measure_dashboard_workloads(repeats=3)

    assert set(results) == {
        "cache_overview_read",
        "replace_rules_read",
        "dashboard_task_render",
    }
    for sample_set in results.values():
        assert sample_set["repeats"] == 3
        assert len(sample_set["samples_ms"]) == 3
        assert sample_set["min_ms"] <= sample_set["median_ms"] <= sample_set["max_ms"]

    assert list(tmp_path.iterdir()) == []
