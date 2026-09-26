"""Merge ZIP batch regression tests for per-item soft-error accounting."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services_impl.pipelines import merge_service


class _FakeUIHandler:
    def set_session(self, session):
        pass


def test_zip_multiple_soft_error_updates_count_one_failure_and_no_success(monkeypatch):
    """同一 ZIP 多個 error update 只歸類一次 failed，且保留錯誤訊息。"""
    monkeypatch.setattr(merge_service, "ensure_pipeline_logging", lambda: None)
    monkeypatch.setattr(merge_service, "UI_LOG_HANDLER", _FakeUIHandler())
    monkeypatch.setattr(
        merge_service,
        "merge_zhcn_to_zhtw_from_zip",
        lambda *args, **kwargs: iter(
            [
                {"error": True, "log": "first decode error"},
                {"error": True, "log": "second write error"},
            ]
        ),
    )
    session = MagicMock()

    results = list(
        merge_service.run_merge_zip_batch_service(
            zip_paths=["broken.zip"],
            output_dir="output",
            session=session,
            only_process_lang=True,
        )
    )

    summary = results[-1]["summary"]
    assert summary["total_zips"] == 1
    assert summary["success_zips"] == 0
    assert summary["failed_zips"] == 1
    assert summary["success_zips"] + summary["failed_zips"] == summary["total_zips"]
    error = summary["failed_zips_list"][0]["error"]
    assert "first decode error" in error
    assert "second write error" in error
