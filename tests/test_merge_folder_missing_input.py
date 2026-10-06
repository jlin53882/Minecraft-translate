"""資料夾合併遇到「輸入資料夾不存在」：必須視為失敗，不能顯示翻譯已完成。"""

from __future__ import annotations

import logging

from app.services_impl.pipelines.merge_service import run_merge_folder_batch_service
from app.tasks.task_session import TaskSession
from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_folder


def test_core_reports_missing_folder_as_error(tmp_path, caplog):
    missing = tmp_path / "nope"
    with caplog.at_level(logging.ERROR):
        updates = list(
            merge_zhcn_to_zhtw_from_folder(str(missing), str(tmp_path / "out"))
        )

    final = updates[-1]
    assert final["error"] is True
    assert "輸入資料夾不存在" in final["log"] and "nope" in final["log"]
    # 同一則訊息後台只出現一次（核心 log 與 yield 的文字相同）
    assert [r.getMessage() for r in caplog.records].count(final["log"]) == 1


def test_service_marks_missing_folder_as_failed_task(tmp_path):
    missing = tmp_path / "nope"
    out = tmp_path / "out"
    session = TaskSession()
    session.start()

    updates = list(
        run_merge_folder_batch_service(
            str(missing), str(out), session, only_process_lang=True
        )
    )

    snap = session.snapshot()
    texts = "\n".join(e.text for e in snap["logs"])
    assert snap["status"] == "ERROR"
    assert snap["error"] is True
    assert "輸入資料夾不存在" in texts
    assert "[階段 1/2 失敗]" in texts and "nope" in texts
    assert "[階段 1/2 完成]" not in texts
    assert "[資料夾] 完成" not in texts
    assert "[階段 2/2 略過]" in texts

    summary = updates[-1].get("summary") or {}
    assert summary.get("failed_folders") == 1
    assert summary.get("success_folders") == 0
    assert summary["failed_folders_list"][0]["name"] == "nope"


def test_one_click_merge_step_tolerates_missing_extract_outputs(tmp_path):
    """一鍵流程：提取沒有產生的來源（例如沒有 Patchouli 書籍）要明確略過，而不是整個流程失敗。"""
    from app.views.pipeline.pipeline_actions import PipelineActions, PipelineServices
    from app.views.pipeline.pipeline_config import PipelineConfig

    seen = []

    def merge_folder(**kw):
        seen.append(kw["skip_missing_input"])
        yield {"progress": 1.0}

    cfg = PipelineConfig(str(tmp_path / "mods"), str(tmp_path / "out"))
    actions = PipelineActions(PipelineServices(merge_folder=merge_folder))
    step = actions.one_click_steps({}, cfg, "dual", ["en_us"], {})[1][2]

    session = TaskSession()
    for _ in step(session):
        pass

    assert seen == [True, True]
