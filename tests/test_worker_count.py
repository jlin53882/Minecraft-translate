"""resolve_worker_count：共通解析規則與各用途的負載上限。"""

from __future__ import annotations

import pytest

from translation_tool.utils.worker_count import (
    MAX_CONFIGURED_WORKERS,
    WORKER_POLICIES,
    resolve_worker_count,
)


def _cfg(value):
    return {"translator": {"parallel_execution_workers": value}}


@pytest.mark.parametrize("value", [1, 4])
def test_valid_small_values_are_used_as_is(value):
    assert resolve_worker_count("count_prescan", _cfg(value), cpu=8) == value
    assert resolve_worker_count("file_pipeline", _cfg(value), cpu=16) == value


def test_high_value_is_capped_by_each_purpose_policy():
    assert resolve_worker_count("count_prescan", _cfg(64), cpu=8) == 64
    assert resolve_worker_count("file_pipeline", _cfg(64), cpu=8) == 4  # CPU 一半
    assert resolve_worker_count("jar_extract", _cfg(64), cpu=8) == 32
    assert resolve_worker_count("moddb_scan", _cfg(64), cpu=8) == 8


def test_extreme_configured_worker_count_has_an_absolute_cap():
    assert (
        resolve_worker_count("cache_load", _cfg(10**9), cpu=8) == MAX_CONFIGURED_WORKERS
    )
    assert (
        resolve_worker_count("jar_browser", _cfg(10**9), cpu=8)
        == MAX_CONFIGURED_WORKERS
    )


def test_low_cpu_count_keeps_at_least_one_worker():
    assert resolve_worker_count("file_pipeline", _cfg(8), cpu=1) == 1
    assert resolve_worker_count("file_pipeline", None, cpu=1) == 1
    assert resolve_worker_count("jar_preview", None, cpu=1) == 1
    assert resolve_worker_count("moddb_scan", _cfg(8), cpu=1) == 1


@pytest.mark.parametrize("bad", [0, -3, "8", "four", 2.0, True, None, [4]])
def test_invalid_values_fall_back_to_the_purpose_default(bad):
    assert resolve_worker_count("count_prescan", _cfg(bad), cpu=8) == 4
    assert resolve_worker_count("icon_index", _cfg(bad), cpu=8) == 8
    assert resolve_worker_count("file_pipeline", _cfg(bad), cpu=8) == 4
    assert resolve_worker_count("jar_preview", _cfg(bad), cpu=8) == 4


def test_missing_or_malformed_config_uses_default():
    assert resolve_worker_count("count_prescan", None, cpu=8) == 4
    assert resolve_worker_count("count_prescan", {}, cpu=8) == 4
    assert resolve_worker_count("count_prescan", {"translator": None}, cpu=8) == 4
    assert resolve_worker_count("count_prescan", {"translator": "x"}, cpu=8) == 4


def test_different_purposes_do_not_have_to_agree():
    config = _cfg(6)
    results = {
        purpose: resolve_worker_count(purpose, config, cpu=8)
        for purpose in WORKER_POLICIES
    }
    assert results["count_prescan"] == 6
    assert results["file_pipeline"] == 4
    assert len(set(results.values())) > 1


def test_unknown_purpose_is_rejected():
    with pytest.raises(KeyError):
        resolve_worker_count("typo", _cfg(4), cpu=8)


def test_cpu_defaults_to_os_cpu_count(monkeypatch):
    monkeypatch.setattr(
        "translation_tool.utils.worker_count.os.cpu_count", lambda: None
    )
    assert resolve_worker_count("file_pipeline", None) == 1  # 取不到視為 2 → 一半
    monkeypatch.setattr("translation_tool.utils.worker_count.os.cpu_count", lambda: 12)
    assert resolve_worker_count("file_pipeline", None) == 6


def test_only_resolver_and_validators_read_the_workers_key():
    """以 AST 找出把設定鍵當完整字串使用的程式（排除 docstring／日誌說明文字）。"""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    allowed = {
        "translation_tool/utils/worker_count.py",
        "translation_tool/utils/config_schema.py",
        "translation_tool/utils/config_manager.py",
        "app/views/config/settings_schema.py",
    }
    offenders = []
    for base in ("app", "translation_tool"):
        for path in (root / base).rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if rel in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if any(
                isinstance(node, ast.Constant)
                and node.value == "parallel_execution_workers"
                for node in ast.walk(tree)
            ):
                offenders.append(rel)
    assert offenders == []


def test_invalid_config_fallback_is_also_capped():
    # 預設值同樣受用途上限約束（moddb_scan 不超過 CPU 核心數）
    assert resolve_worker_count("moddb_scan", None, cpu=2) == 2
    assert resolve_worker_count("moddb_scan", _cfg(0), cpu=1) == 1
    assert resolve_worker_count("moddb_scan", _cfg("8"), cpu=2) == 2
