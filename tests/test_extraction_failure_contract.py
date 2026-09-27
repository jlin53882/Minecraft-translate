"""PR #104 Finding 2：無法處理的 JAR 必須讓提取步驟失敗，流水線不可繼續。

原本 _run_extraction_with_session 只看 GLOBAL_LOG_LIMITER.filter() 後的 error，
而 filter 只保留 log / progress；最終 stats.failures > 0 也照樣 finish() → DONE，
一鍵流水線便以不完整的提取結果繼續 merge / translate / bundle。
"""

import zipfile

from app.logging.task_session import TaskSession
from app.services_impl.pipelines import extract_service
from app.services_impl.pipelines.extract_service import _run_extraction_with_session


def _final(failures, **extra):
    return {
        "progress": 1.0,
        "log": "--- 提取完成！ ---",
        "stats": {"success": 5, "warnings": 0, "failures": failures, "total_files": 5},
        **extra,
    }


def test_final_stats_failures_mark_session_error():
    session = TaskSession()
    session.start()
    gen = iter(
        [
            {"progress": 0.5, "log": "[1/2] good.jar"},
            {
                "progress": 1.0,
                "log": "[ERROR] 無法提取 broken.jar（JAR 可能已損毀或無法讀取）",
            },
            _final(1),
        ]
    )

    _run_extraction_with_session(gen, session, "Lang")

    assert session.error is True
    assert session.snapshot()["status"] == "ERROR"
    assert session.summary["failures"] == 1
    session.finish()  # 呼叫端的 finally 也不可把 ERROR 蓋成 DONE
    assert session.snapshot()["status"] == "ERROR"


def test_no_failures_still_done():
    session = TaskSession()
    session.start()
    _run_extraction_with_session(iter([_final(0)]), session, "Lang")
    assert session.error is False
    assert session.snapshot()["status"] == "DONE"


def test_dual_mode_phase_failures_are_counted():
    """dual：lang 階段有失敗、book 階段沒有，也要算失敗（不可只看最後一個 phase）。"""
    session = TaskSession()
    session.start()
    gen = iter(
        [
            {**_final(1), "phase": "lang"},
            {**_final(0), "phase": "book"},
        ]
    )
    _run_extraction_with_session(gen, session, "Dual")
    assert session.error is True


def test_explicit_error_flag_is_not_lost_through_limiter():
    session = TaskSession()
    session.start()
    gen = iter([{"progress": 0.0, "error": True, "log": "[錯誤] 掃描失敗: boom"}])
    _run_extraction_with_session(gen, session, "Lang")
    assert session.error is True


def test_real_corrupted_jar_makes_lang_extraction_error(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    mods.mkdir()
    with zipfile.ZipFile(mods / "good-1.0.jar", "w") as zf:
        zf.writestr("assets/good/lang/en_us.json", '{"a": "b"}')
    (mods / "broken-1.0.jar").write_bytes(b"not a zip")

    session = TaskSession()
    extract_service.run_lang_extraction_service(
        str(mods), str(tmp_path / "out"), session
    )

    assert session.error is True
    assert session.snapshot()["status"] == "ERROR"
    # 已成功提取的檔案保留（部分輸出可以存在，只是步驟不能算成功）
    assert list((tmp_path / "out").rglob("en_us.json"))


def test_one_click_pipeline_stops_after_failed_extraction(tmp_path, monkeypatch):
    from app.views.pipeline import pipeline_view
    from tests.conftest import mock_filepicker, mock_page

    calls = []

    def fake_lang_extraction(mods_dir, output_dir, session, lang_codes=None):
        # 走 production 的提取 session 處理（generator 最終回報 1 個 JAR 失敗）
        _run_extraction_with_session(iter([_final(1)]), session, "Lang")

    def fake_merge(**kwargs):
        calls.append("merge")
        yield {"progress": 1.0}

    monkeypatch.setattr(
        pipeline_view, "run_lang_extraction_service", fake_lang_extraction
    )
    monkeypatch.setattr(pipeline_view, "run_merge_folder_batch_service", fake_merge)
    monkeypatch.setattr(
        pipeline_view,
        "run_lm_translation_service",
        lambda **k: calls.append("translate"),
    )
    monkeypatch.setattr(
        pipeline_view,
        "build_bundle_staging",
        lambda *a, **k: calls.append("bundle") or {"copied": 1, "merged": 0},
    )

    class _SyncThread:
        def __init__(self, target=None, daemon=None, **kw):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(pipeline_view.threading, "Thread", _SyncThread)

    (tmp_path / "mods").mkdir()
    (tmp_path / "out").mkdir()
    page = mock_page()
    view = pipeline_view.PipelineView(page, mock_filepicker())
    view.input_path_text.value = str(tmp_path / "mods")
    view.output_path_text.value = str(tmp_path / "out")

    view._on_one_click_execute({"mode": "lang", "lang_codes": ["en_us"]})

    assert calls == []  # merge / translate / bundle 都不可執行
    # 執行排入 event loop 的 UI 更新後，步驟晶片應顯示失敗
    import asyncio

    while page._tasks:
        handler, args = page._tasks.pop(0)
        asyncio.run(handler(*args))
    assert view.progress_panel.steps[0].status == "failed"
    assert view.progress_panel.steps[1].status == "waiting"
