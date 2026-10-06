"""``TaskSession.finish()`` 冪等、輸入路徑型別契約、repr/str 例外訊息的後台去重。"""

from __future__ import annotations

import inspect
import logging
import zipfile

import pytest

from app import services as qc_services
from app.services_impl.pipelines import bundle_service
from app.services_impl.pipelines.merge_service import run_merge_folder_batch_service
from app.tasks import task_session as task_session_module
from app.tasks.task_session import TaskSession
from translation_tool.core import lang_merge_extracted_assets as merge_ext
from translation_tool.core.lang_merger import (
    merge_zhcn_to_zhtw_from_folder,
    merge_zhcn_to_zhtw_from_zip,
)
from translation_tool.utils import ui_mirror
from translation_tool.utils.ui_mirror import mirror_lines


@pytest.fixture(autouse=True)
def _reset_tracker():
    ui_mirror.BACKEND_SEEN_TRACKER.clear()
    yield
    ui_mirror.BACKEND_SEEN_TRACKER.clear()


def _end_records(caplog, name):
    return [
        r
        for r in caplog.records
        if getattr(r, "task_name", None) == name
        and r.getMessage().startswith("任務結束")
    ]


# ---------------------------------------------------------------- finish() 冪等


def test_finish_twice_writes_one_end_record_and_notifies_once(caplog):
    events = []
    observer = lambda _s, ev: events.append(ev)
    task_session_module.add_observer(observer)
    try:
        session = TaskSession(name="冪等")
        with caplog.at_level(logging.INFO):
            session.start()
            session.finish()
            session.finish()
    finally:
        task_session_module.remove_observer(observer)

    assert len(_end_records(caplog, "冪等")) == 1
    assert events.count("finish") == 1
    assert session.status == "DONE"


def test_second_finish_after_set_error_keeps_error_without_a_second_record(caplog):
    session = TaskSession(name="冪等錯誤")
    with caplog.at_level(logging.INFO):
        session.start()
        session.finish()  # 步驟自己先 finish
        session.set_error()  # 流水線安全網
        session.finish()

    assert session.status == "ERROR"
    assert len(_end_records(caplog, "冪等錯誤")) == 1


def test_start_rearms_the_terminal_notification(caplog):
    session = TaskSession(name="重啟")
    with caplog.at_level(logging.INFO):
        session.start()
        session.finish()
        session.start()
        session.finish()
    assert len(_end_records(caplog, "重啟")) == 2


def test_pipeline_step_exception_then_runner_safety_net_ends_once(caplog):
    """一鍵步驟例外：步驟 finally 先 finish，PipelineRunner 的安全網再 set_error + finish。"""
    from app.views.pipeline.pipeline_actions import PipelineActions, PipelineServices
    from app.views.pipeline.pipeline_config import PipelineConfig

    def merge_folder(**_kw):
        raise RuntimeError("merge exploded")
        yield  # pragma: no cover - 讓它成為 generator

    cfg = PipelineConfig("mods_dir", "out_dir")
    step = PipelineActions(PipelineServices(merge_folder=merge_folder)).one_click_steps(
        {}, cfg, "lang", ["en_us"], {}
    )[1][2]
    session = TaskSession(name="一鍵合併")

    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError):
            for _ in step(session):
                pass
        session.set_error()  # PipelineRunner.run_step 的安全網
        session.finish()

    assert len(_end_records(caplog, "一鍵合併")) == 1
    assert session.status == "ERROR"


# ---------------------------------------------------------------- 輸入型別契約


def test_existing_file_passed_as_folder_is_an_error_not_a_success(tmp_path):
    not_a_dir = tmp_path / "input.txt"
    not_a_dir.write_text("x", encoding="utf-8")

    updates = list(
        merge_zhcn_to_zhtw_from_folder(str(not_a_dir), str(tmp_path / "out"))
    )

    assert updates[-1]["error"] is True
    assert "輸入路徑不是資料夾" in updates[-1]["log"]


def test_existing_zip_passed_as_folder_is_an_error(tmp_path):
    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.txt", "x")

    updates = list(merge_zhcn_to_zhtw_from_folder(str(zip_path), str(tmp_path / "out")))
    assert updates[-1]["error"] is True


def test_directory_passed_as_zip_is_an_error(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()

    updates = list(merge_zhcn_to_zhtw_from_zip(str(folder), str(tmp_path / "out")))

    assert updates[-1]["error"] is True
    assert "輸入路徑不是 ZIP 檔案" in updates[-1]["log"]


@pytest.mark.parametrize("skip_missing_input", [False, True])
def test_service_fails_when_folder_input_is_an_existing_file(
    tmp_path, skip_missing_input
):
    """即使一鍵流程用 skip_missing_input=True，存在但型別錯誤的路徑也不能被當成「略過」。"""
    not_a_dir = tmp_path / "input.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    session = TaskSession()
    session.start()

    updates = list(
        run_merge_folder_batch_service(
            str(not_a_dir),
            str(tmp_path / "out"),
            session,
            only_process_lang=True,
            skip_missing_input=skip_missing_input,
        )
    )

    snap = session.snapshot()
    texts = "\n".join(e.text for e in snap["logs"])
    assert snap["status"] == "ERROR"
    assert "輸入路徑不是資料夾" in texts
    assert "略過" not in texts.split("[階段 2/2")[0]
    assert "[階段 1/2 完成]" not in texts
    summary = updates[-1]["summary"]
    assert summary["failed_folders"] == 1 and summary["success_folders"] == 0


# ---------------------------------------------------------------- repr/str 後台只一次


def _count(caplog, needle):
    return sum(needle in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("service", "generator_name", "needle"),
    [
        (
            "run_untranslated_check_service",
            "check_untranslated_generator",
            "未翻譯檢查失敗",
        ),
        ("run_variant_compare_service", "compare_variants_generator", None),
        ("run_english_residue_check_service", "check_english_residue_generator", None),
        ("run_variant_compare_tsv_service", "compare_variants_tsv_generator", None),
    ],
)
def test_qc_fatal_exception_is_written_to_backend_once(
    monkeypatch, caplog, service, generator_name, needle
):
    def boom(*_a, **_k):
        raise ValueError("bad")
        yield  # pragma: no cover

    monkeypatch.setattr(qc_services, generator_name, boom)
    fn = getattr(qc_services, service)
    args = tuple("x" for _ in inspect.signature(fn).parameters)

    with caplog.at_level(logging.INFO):
        updates = list(fn(*args))
        # QCBase 對每個 update 的 log 做的事
        for u in updates:
            lines = [
                (ln, "error")
                for ln in str(u.get("log") or "").split("\n")
                if ln.strip()
            ]
            mirror_lines(lines, prefix="[QC] ")

    fatal_lines = [
        r.getMessage().split("\n")[0]
        for r in caplog.records
        if "[致命錯誤]" in r.getMessage()
    ]
    assert len(fatal_lines) == 1, fatal_lines
    assert "ValueError('bad')" in fatal_lines[0]


def test_bundle_service_fatal_exception_is_written_to_backend_once(monkeypatch, caplog):
    def boom(*_a, **_k):
        raise RuntimeError("zip failed")
        yield  # pragma: no cover

    monkeypatch.setattr(bundle_service, "bundle_outputs_generator", boom)
    session = TaskSession(name="打包")

    with caplog.at_level(logging.INFO):
        for update in bundle_service.run_bundling_service("root", "out.zip"):
            if update.get("log"):
                session.add_log(update["log"])  # PipelineActions.bundle 的轉送

    assert _count(caplog, "[致命錯誤] 打包服務失敗") == 1


def test_merge_ext_assets_fatal_exception_is_written_to_backend_once(
    monkeypatch, caplog, tmp_path
):
    def boom(*_a, **_k):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(merge_ext, "_scan_extracted_lang_files", boom)
    out = tmp_path / "lang_output"
    out.mkdir()
    session = TaskSession(name="階段2")

    with caplog.at_level(logging.INFO):
        updates = list(merge_ext.merge_extracted_to_assets(out, session))

    assert updates[-1]["error"] is True
    assert _count(caplog, "[MergeExt→Assets] 錯誤") == 1
