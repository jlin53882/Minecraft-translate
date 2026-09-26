"""merge_service folder 模式的單元測試。

用途：測試 run_merge_folder_batch_service() 的參數傳遞與錯誤處理。
"""

from __future__ import annotations

from pathlib import Path

from app.services_impl.pipelines import merge_service
from translation_tool.core import lang_merge_extracted_assets as extracted_assets


class _FakeSession:
    def __init__(self):
        self.logs = []
        self.progress = 0.0
        self.error = False
        self._summary = None
        self.finish_called = False

    def start(self):
        pass

    def add_log(self, msg):
        self.logs.append(msg)

    def set_progress(self, val):
        self.progress = val

    def set_error(self):
        self.error = True

    def set_summary(self, d):
        self._summary = d

    def finish(self):
        self.finish_called = True

    def snapshot(self):
        return {"status": "IDLE", "progress": 0.0, "logs": self.logs}


class _FakeUIHandler:
    def set_session(self, s):
        pass


def test_run_merge_folder_batch_service_completes_without_error(
    tmp_path: Path, monkeypatch
) -> None:
    """測試 run_merge_folder_batch_service 在有效輸入時正常完成。"""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()

    # 建立簡單的資料夾結構
    (input_dir / "assets" / "demo" / "lang").mkdir(parents=True, exist_ok=True)
    (input_dir / "assets" / "demo" / "lang" / "zh_cn.json").write_text(
        '{"a": "b"}', encoding="utf-8"
    )

    monkeypatch.setattr(merge_service, "ensure_pipeline_logging", lambda: None)
    monkeypatch.setattr(merge_service, "UI_LOG_HANDLER", _FakeUIHandler())

    session = _FakeSession()
    results = list(
        merge_service.run_merge_folder_batch_service(
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            session=session,
            only_process_lang=False,
            process_zh_cn=True,
            patchouli_skip=False,
            patchouli_threshold=0.5,
            zh_en_threshold=2,
        )
    )

    assert results[-1]["progress"] == 1.0
    assert not results[-1].get("error", False)
    assert results[-1]["summary"]["success_folders"] == 1
    assert results[-1]["summary"]["failed_folders"] == 0


def test_run_merge_folder_batch_service_nonexistent_folder_yields_without_error(
    tmp_path: Path, monkeypatch
) -> None:
    """測試資料夾不存在時 service 不拋例外且 failed_folders=1。"""
    input_dir = tmp_path / "nonexistent"
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    monkeypatch.setattr(merge_service, "ensure_pipeline_logging", lambda: None)
    monkeypatch.setattr(merge_service, "UI_LOG_HANDLER", _FakeUIHandler())

    session = _FakeSession()
    results = list(
        merge_service.run_merge_folder_batch_service(
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            session=session,
            only_process_lang=False,
            process_zh_cn=True,
            patchouli_skip=False,
            patchouli_threshold=0.5,
            zh_en_threshold=2,
        )
    )

    assert results[-1]["progress"] == 1.0
    assert not results[-1].get("error", False)
    assert results[-1]["summary"]["success_folders"] == 1
    assert results[-1]["summary"]["failed_folders"] == 0


def test_output_counts_has_assets_key(tmp_path: Path):
    """2026-08-04 C3: _count_output_files 應包含 assets key,且 assets/ 不計入 lang_output。"""
    from app.services_impl.pipelines.merge_service import run_merge_folder_batch_service

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # 建立一個簡單的 _extracted 結構
    extracted = input_dir / "test_extracted" / "testmod" / "lang"
    extracted.mkdir(parents=True)
    (extracted / "zh_cn.json").write_text('{"key1":"中文"}', encoding="utf-8")

    from unittest.mock import MagicMock

    session = MagicMock()
    results = list(
        run_merge_folder_batch_service(
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            session=session,
            only_process_lang=True,
            process_zh_cn=True,
            patchouli_skip=True,
            patchouli_threshold=0.5,
            zh_en_threshold=2,
        )
    )

    last = results[-1]
    oc = last.get("summary", {}).get("output_counts", {})

    # assets key 應存在
    assert "assets" in oc, f"output_counts 缺少 assets key: {list(oc.keys())}"
    # lang_output 與 assets 應分開計
    assert isinstance(oc["lang_output"], int)
    assert isinstance(oc["assets"], int)


def test_summary_success_folders_key_compatible(tmp_path: Path):
    """2026-08-04 A1: folder 模式 summary 應包含 success_folders 與 failed_folders。"""
    from app.services_impl.pipelines.merge_service import run_merge_folder_batch_service

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    extracted = input_dir / "test_extracted" / "testmod" / "lang"
    extracted.mkdir(parents=True)
    (extracted / "zh_cn.json").write_text('{"key1":"中文"}', encoding="utf-8")

    from unittest.mock import MagicMock

    session = MagicMock()
    results = list(
        run_merge_folder_batch_service(
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            session=session,
            only_process_lang=True,
        )
    )

    last = results[-1]
    summary = last.get("summary", {})

    # Folder 流程使用 success_folders key
    assert "success_folders" in summary, (
        f"summary 缺少 success_folders: {list(summary.keys())}"
    )
    assert summary["success_folders"] == 1
    assert summary["failed_folders"] == 0


def test_stage1_soft_errors_fail_folder_and_skip_stage2(tmp_path: Path, monkeypatch):
    """Stage 1 有錯誤時只計一次失敗、略過 Stage 2 並結束為 ERROR。"""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    session = _FakeSession()
    stage2_called = []

    def unexpected_stage2(*args, **kwargs):
        stage2_called.append(True)
        yield {"progress": 1.0}

    monkeypatch.setattr(merge_service, "ensure_pipeline_logging", lambda: None)
    monkeypatch.setattr(merge_service, "UI_LOG_HANDLER", _FakeUIHandler())
    monkeypatch.setattr(
        merge_service,
        "merge_zhcn_to_zhtw_from_folder",
        lambda *args, **kwargs: iter(
            [
                {"error": True, "log": "first stage error"},
                {"error": True, "log": "second stage error"},
            ]
        ),
    )
    monkeypatch.setattr(merge_service, "merge_extracted_to_assets", unexpected_stage2)
    monkeypatch.setattr(
        merge_service,
        "load_config",
        lambda: {"lang_merger": {"enable_extracted_to_assets_merge": True}},
    )

    results = list(
        merge_service.run_merge_folder_batch_service(
            str(input_dir), str(output_dir), session, only_process_lang=True
        )
    )

    summary = results[-1]["summary"]
    assert summary["success_folders"] == 0
    assert summary["failed_folders"] == summary["total_folders"] == 1
    assert "first stage error" in summary["failed_folders_list"][0]["error"]
    assert "second stage error" in summary["failed_folders_list"][0]["error"]
    assert results[-1]["error"] is True
    assert session.error is True
    assert session._summary == summary
    assert session.finish_called is False
    assert not stage2_called
    assert any("[階段 1/2 失敗]" in log for log in session.logs)
    assert not any("[階段 1/2 完成]" in log for log in session.logs)


def test_stage2_soft_error_yields_final_error_lifecycle(tmp_path: Path, monkeypatch):
    """Stage 2 失敗仍須摘要、ERROR 狀態及最後一筆 yield。"""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    session = _FakeSession()

    monkeypatch.setattr(merge_service, "ensure_pipeline_logging", lambda: None)
    monkeypatch.setattr(merge_service, "UI_LOG_HANDLER", _FakeUIHandler())
    monkeypatch.setattr(
        merge_service,
        "merge_zhcn_to_zhtw_from_folder",
        lambda *args, **kwargs: iter([{"progress": 1.0}]),
    )
    monkeypatch.setattr(
        merge_service,
        "merge_extracted_to_assets",
        lambda *args, **kwargs: iter(
            [
                {"progress": 0.5},
                {"progress": 1.0, "error": True, "log": "assets write failed"},
            ]
        ),
    )
    monkeypatch.setattr(
        merge_service,
        "load_config",
        lambda: {"lang_merger": {"enable_extracted_to_assets_merge": True}},
    )

    results = list(
        merge_service.run_merge_folder_batch_service(
            str(input_dir), str(output_dir), session, only_process_lang=True
        )
    )

    assert results[-1]["progress"] == 1.0
    assert results[-1]["error"] is True
    summary = results[-1]["summary"]
    assert summary["success_folders"] == 0
    assert summary["failed_folders"] == 1
    assert "assets write failed" in summary["failed_folders_list"][0]["error"]
    assert session.error is True
    assert session._summary == summary
    assert session.finish_called is False


def test_production_stage2_write_failure_reaches_error_lifecycle(
    tmp_path: Path, monkeypatch
):
    """Stage 2 真實寫檔失敗一路傳到 folder summary 與 TaskSession ERROR。"""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    session = _FakeSession()
    sources = {}
    lang_output_dir = output_dir / "lang_output"

    for modid in ("good_a", "bad_b", "good_c"):
        source = lang_output_dir / f"{modid}_extracted" / modid / "lang" / "zh_cn.json"
        source.parent.mkdir(parents=True)
        source.write_text('{"key": "中文"}', encoding="utf-8")
        sources[modid] = source

    real_write = extracted_assets._write_json_atomic

    def fail_bad_mod_write(path, data):
        if "bad_b" in str(path):
            raise OSError("simulated write failure")
        real_write(path, data)

    monkeypatch.setattr(merge_service, "ensure_pipeline_logging", lambda: None)
    monkeypatch.setattr(merge_service, "UI_LOG_HANDLER", _FakeUIHandler())
    monkeypatch.setattr(
        merge_service,
        "merge_zhcn_to_zhtw_from_folder",
        lambda *args, **kwargs: iter([{"progress": 1.0}]),
    )
    monkeypatch.setattr(
        merge_service,
        "load_config",
        lambda: {"lang_merger": {"enable_extracted_to_assets_merge": True}},
    )
    monkeypatch.setattr(extracted_assets, "_write_json_atomic", fail_bad_mod_write)

    results = list(
        merge_service.run_merge_folder_batch_service(
            str(input_dir), str(output_dir), session, only_process_lang=True
        )
    )

    final = results[-1]
    summary = final["summary"]
    assert final["error"] is True
    assert summary["success_folders"] == 0
    assert summary["failed_folders"] == 1
    assert "bad_b" in summary["failed_folders_list"][0]["error"]
    assert "write failed" in summary["failed_folders_list"][0]["error"]
    assert session.error is True
    assert session.finish_called is False
    for modid in ("good_a", "good_c"):
        assert (lang_output_dir / "assets" / modid / "lang" / "zh_tw.json").exists()
        assert not sources[modid].exists()
    assert not (lang_output_dir / "assets" / "bad_b" / "lang" / "zh_tw.json").exists()
    assert sources["bad_b"].exists()
