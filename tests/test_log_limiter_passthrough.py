"""LogLimiter 不可剝掉 log / progress 以外的欄位。

原本 `LogLimiter.filter()` 對帶有 `log` 的 update 只回傳 `{"log", "progress"}`，
其餘欄位（`error`、`result`、`stats`…）全部遺失。因此所有「先過 limiter、
再看 error / result」的 service 都會出錯：

- QC 檢查（app/services.py）：錯誤被當成成功
- 打包（run_bundling_service）：輸入不存在 / ZIP 失敗時，流水線打包步驟仍算完成
- 批次學名查詢：最後一筆的 `result` 被剝掉，查詢結果不會顯示

這些測試使用真正的 GLOBAL_LOG_LIMITER（不 monkeypatch filter），
確保 service 的實際輸出帶有 error / result。
"""

from __future__ import annotations

import time

import pytest

from app import services as qc_services
from app.services_impl.logging_service import GLOBAL_LOG_LIMITER, LogLimiter
from app.services_impl.pipelines import bundle_service, lookup_service
from app.tasks.task_session import TaskSession


@pytest.fixture(autouse=True)
def _clean_global_limiter():
    GLOBAL_LOG_LIMITER.flush()
    yield
    GLOBAL_LOG_LIMITER.flush()


# ---------------------------------------------------------------- LogLimiter


def test_filter_keeps_extra_keys_with_log():
    limiter = LogLimiter(flush_interval=0.0)

    out = limiter.filter(
        {
            "log": "boom",
            "progress": 1.0,
            "error": True,
            "result": "r",
            "stats": {"a": 1},
        }
    )

    assert out is not None
    assert out["log"] == "boom"
    assert out["progress"] == 1.0
    assert out["error"] is True
    assert out["result"] == "r"
    assert out["stats"] == {"a": 1}


def test_throttled_update_with_error_is_forwarded_immediately():
    """節流期間的 error 不可被吞掉，且要帶上先前累積的 log。"""
    limiter = LogLimiter(flush_interval=60.0)
    limiter.last_flush = time.time()

    assert limiter.filter({"log": "step 1", "progress": 0.1}) == {"progress": 0.1}
    out = limiter.filter({"log": "failed", "progress": 1.0, "error": True})

    assert out is not None
    assert out["error"] is True
    assert out["log"] == "step 1\nfailed"
    assert out["progress"] == 1.0
    assert limiter.flush() is None  # 已一併送出，不會重複


def test_plain_log_updates_are_still_throttled():
    limiter = LogLimiter(flush_interval=60.0)
    limiter.last_flush = time.time()

    assert limiter.filter({"log": "a"}) is None
    assert limiter.filter({"log": "b"}) is None
    assert limiter.flush()["log"] == "a\nb"


# ---------------------------------------------------------------- QC services


@pytest.mark.parametrize(
    ("service", "args"),
    [
        (
            qc_services.run_untranslated_check_service,
            ("{missing}", "{missing}", "{out}"),
        ),
        (
            qc_services.run_variant_compare_tsv_service,
            ("{missing}/x.tsv", "{out}/o.csv"),
        ),
    ],
)
def test_qc_service_error_survives_limiter(tmp_path, service, args):
    missing = tmp_path / "missing"
    out = tmp_path / "out"
    out.mkdir()
    real_args = [a.format(missing=missing, out=out) for a in args]

    updates = list(service(*real_args))

    assert any(u.get("error") for u in updates), updates


# ---------------------------------------------------------------- bundle


def test_bundling_service_error_survives_limiter(tmp_path):
    updates = list(
        bundle_service.run_bundling_service(
            str(tmp_path / "missing"), str(tmp_path / "out.zip")
        )
    )

    assert any(u.get("error") for u in updates), updates


def test_pipeline_bundle_step_fails_when_input_missing(tmp_path):
    """一鍵流水線的打包步驟：輸入不存在時 session 必須是 ERROR。"""
    from app.views.pipeline.pipeline_actions import PipelineActions

    session = TaskSession()
    session.start()
    list(
        PipelineActions().bundle(
            session,
            input_root_dir=str(tmp_path / "missing"),
            output_zip_path=str(tmp_path / "out.zip"),
        )
    )

    assert session.snapshot()["status"] == "ERROR"


# ---------------------------------------------------------------- lookup


def test_batch_lookup_result_survives_limiter(monkeypatch):
    monkeypatch.setattr(lookup_service, "is_potential_species_name", lambda n: True)
    monkeypatch.setattr(lookup_service, "lookup_species_name", lambda n: f"{n}-zh")

    updates = list(lookup_service.run_batch_lookup_service('["Canis lupus"]'))

    results = [u["result"] for u in updates if u.get("result")]
    assert results, updates
    assert "Canis lupus-zh" in results[-1]
