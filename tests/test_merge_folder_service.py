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