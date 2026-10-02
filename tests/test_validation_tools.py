"""專案內 UI 與效能驗證工具的測試。"""

from __future__ import annotations

import json
import zipfile

import pytest

from tools import performance_baseline, ui_smoke


def test_parse_viewports_accepts_matrix_and_rejects_invalid_values():
    assert ui_smoke.parse_viewports(["1360x900", "1100x720"]) == (
        (1360, 900),
        (1100, 720),
    )
    with pytest.raises(ValueError):
        ui_smoke.parse_viewports(["wide"])


def test_find_browser_executable_honors_explicit_path(tmp_path):
    browser = tmp_path / "browser.exe"
    browser.write_bytes(b"fixture")
    assert ui_smoke.find_browser_executable(str(browser)) == browser


def test_build_synthetic_jars_creates_nested_deterministic_fixtures(tmp_path):
    jars = performance_baseline.build_synthetic_jars(tmp_path, 12)
    assert len(jars) == 12
    assert any(path.parent.name == "nested" for path in jars)
    with zipfile.ZipFile(jars[0]) as archive:
        assert "assets/fixture/lang/en_us.json" in archive.namelist()


def test_summarize_samples_reports_median_and_range():
    assert performance_baseline.summarize_samples([3.0, 1.0, 2.0]) == {
        "median_ms": 2.0,
        "min_ms": 1.0,
        "max_ms": 3.0,
    }


def test_summarize_ui_report_groups_view_build_timings(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "kind": "view",
                        "view": "dashboard",
                        "startup_ms": 10,
                        "browser_ready_ms": 100,
                        "server_build_ms": 2,
                    },
                    {
                        "kind": "view",
                        "view": "dashboard",
                        "startup_ms": 12,
                        "browser_ready_ms": 120,
                        "server_build_ms": 4,
                    },
                    {"kind": "dialog", "view": "snackbar"},
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = performance_baseline.summarize_ui_report(report_path)

    assert summary["startup"]["median_ms"] == 11.0
    assert summary["browser_ready"]["median_ms"] == 110.0
    assert summary["first_view_build"]["dashboard"]["median_ms"] == 3.0
