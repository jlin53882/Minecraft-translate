"""resolve_worker_count：共通解析規則與各用途的負載上限。"""

from __future__ import annotations

import pytest

from translation_tool.utils.worker_count import WORKER_POLICIES, resolve_worker_count


def _cfg(value):
    return {"translator": {"parallel_execution_workers": value}}


@pytest.mark.parametrize("value", [1, 4])
def test_valid_small_values_are_used_as_is(value):
    assert resolve_worker_count("count_prescan", _cfg(value), cpu=8) == value
    assert resolve_worker_count("file_pipeline", _cfg(value), cpu=16) == value


def test_high_value_is_capped_only_for_purposes_with_a_cap():
    assert resolve_worker_count("count_prescan", _cfg(64), cpu=8) == 64  # 無上限
    assert resolve_worker_count("file_pipeline", _cfg(64), cpu=8) == 4  # CPU 一半
    assert resolve_worker_count("jar_extract", _cfg(64), cpu=8) == 32
    assert resolve_worker_count("moddb_scan", _cfg(64), cpu=8) == 8


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
    """讀取 parallel_execution_workers 的程式必須經過 resolve_worker_count。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    allowed = {
        "translation_tool/utils/worker_count.py",
        "translation_tool/utils/config_schema.py",
        "translation_tool/utils/config_manager.py",
        "app/views/config/settings_schema.py",
        # 只在日誌/說明文字中提到設定名稱
        "translation_tool/core/jar_processor_preview.py",
    }
    offenders = []
    for base in ("app", "translation_tool"):
        for path in (root / base).rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if rel in allowed:
                continue
            if "parallel_execution_workers" in path.read_text(encoding="utf-8"):
                offenders.append(rel)
    assert offenders == []
