"""單步 Pipeline 合併：使用者填一個「不存在」的輸出資料夾，取消後新建的半成品要被清掉。

以前對話框（ensure_output_dir）與 PipelineActions.merge（os.makedirs）都先建立了輸出資料夾，
服務看到的「原本存在」永遠是 True，取消時就不清理。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services_impl.pipelines import merge_service
from app.tasks.task_session import TaskSession
from app.ui.safe_file_picker import check_output_dir
from app.views.pipeline.pipeline_actions import PipelineActions
from translation_tool.utils.cancellation import TaskCancelled


def test_check_output_dir_validates_without_creating(tmp_path):
    target = tmp_path / "new" / "deeper" / "out"

    assert check_output_dir(str(target)) is None
    assert not (tmp_path / "new").exists()


def test_check_output_dir_rejects_empty_file_and_uncreatable(tmp_path):
    assert check_output_dir("  ") == "請輸入輸出目錄"
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert "是檔案" in check_output_dir(str(f))
    assert check_output_dir(str(f / "sub")) == "輸出目錄無法建立"


def test_check_output_dir_accepts_an_existing_directory(tmp_path):
    assert check_output_dir(str(tmp_path)) is None


def _fake_core_creating_output_then_cancelling(session):
    def core(input_dir, output_dir, *args, **kwargs):
        out = Path(output_dir)
        (out / "lang_output").mkdir(parents=True)  # 核心已寫出一些半成品
        session.request_cancel()
        yield {"progress": 0.1, "log": "寫了一些檔案"}
        yield {"progress": 0.2, "log": "不該處理到這裡"}

    return core


def test_actions_merge_does_not_create_the_output_directory(tmp_path):
    session = TaskSession()
    out = tmp_path / "out"
    in_dir = tmp_path / "in"
    in_dir.mkdir()

    gen = PipelineActions().merge(
        session,
        str(in_dir),
        str(out),
        "folder",
        only_lang=True,
        process_zh_cn=True,
        patchouli_skip=False,
        patchouli_threshold=0.5,
        zh_en_threshold=2,
    )

    assert not out.exists()  # 還沒開始消耗 generator：不得已經建立
    gen.close()


def test_standalone_pipeline_merge_cancel_removes_the_new_output(tmp_path, monkeypatch):
    session = TaskSession()
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out = tmp_path / "new_out"
    monkeypatch.setattr(
        merge_service,
        "merge_zhcn_to_zhtw_from_folder",
        _fake_core_creating_output_then_cancelling(session),
    )

    gen = PipelineActions().merge(
        session,
        str(in_dir),
        str(out),
        "folder",
        only_lang=True,
        process_zh_cn=True,
        patchouli_skip=False,
        patchouli_threshold=0.5,
        zh_en_threshold=2,
    )

    with pytest.raises(TaskCancelled):
        list(gen)

    assert not out.exists()  # 這次新建的輸出被清掉


def test_standalone_pipeline_merge_cancel_keeps_a_preexisting_output(
    tmp_path, monkeypatch
):
    session = TaskSession()
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out = tmp_path / "existing_out"
    out.mkdir()
    (out / "keep.txt").write_text("使用者原本的檔案", encoding="utf-8")
    monkeypatch.setattr(
        merge_service,
        "merge_zhcn_to_zhtw_from_folder",
        _fake_core_creating_output_then_cancelling(session),
    )

    gen = PipelineActions().merge(
        session,
        str(in_dir),
        str(out),
        "folder",
        only_lang=True,
        process_zh_cn=True,
        patchouli_skip=False,
        patchouli_threshold=0.5,
        zh_en_threshold=2,
    )
    with pytest.raises(TaskCancelled):
        list(gen)

    assert (out / "keep.txt").exists()  # 原本就存在的資料夾不動
