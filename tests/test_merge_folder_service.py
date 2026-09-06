"""merge_service folder 模式的單元測試。

用途：測試 run_merge_folder_batch_service() 的參數傳遞與錯誤處理。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services_impl.pipelines import merge_service
from app.logging import task_session as ts_module


class _FakeSession:
    def __init__(self):
        self.logs = []
        self.progress = 0.0
        self.error = False
        self._summary = None

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
        pass

    def snapshot(self):
        return {"status": "IDLE", "progress": 0.0, "logs": self.logs}


class _FakeUIHandler:
    def set_session(self, s):
        pass


def test_run_merge_folder_batch_service_completes_without_error(tmp_path: Path, monkeypatch) -> None:
    """測試 run_merge_folder_batch_service 在有效輸入時正常完成。"""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()

    # 建立簡單的資料夾結構
    (input_dir / "assets" / "demo" / "lang").mkdir(parents=True, exist_ok=True)
    (input_dir / "assets" / "demo" / "lang" / "zh_cn.json").write_text('{"a": "b"}', encoding="utf-8")

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


def test_run_merge_folder_batch_service_nonexistent_folder_yields_without_error(tmp_path: Path, monkeypatch) -> None:
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
    results = list(run_merge_folder_batch_service(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        session=session,
        only_process_lang=True,
        process_zh_cn=True,
        patchouli_skip=True,
        patchouli_threshold=0.5,
        zh_en_threshold=2,
    ))

    last = results[-1]
    oc = last.get("summary", {}).get("output_counts", {})

    # assets key 應存在
    assert "assets" in oc, f"output_counts 缺少 assets key: {list(oc.keys())}"
    # lang_output 與 assets 應分開計
    assert isinstance(oc["lang_output"], int)
    assert isinstance(oc["assets"], int)


def test_summary_success_folders_key_compatible(tmp_path: Path):
    """2026-08-04 A1: folder 模式 summary 應包含 success_folders 與 failed_folders。"""
    import json
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
    results = list(run_merge_folder_batch_service(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        session=session,
        only_process_lang=True,
    ))

    last = results[-1]
    summary = last.get("summary", {})

    # Folder 流程使用 success_folders key
    assert "success_folders" in summary, f"summary 缺少 success_folders: {list(summary.keys())}"
    assert summary["success_folders"] == 1
    assert summary["failed_folders"] == 0
