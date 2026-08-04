"""Stage 1 + Stage 2 e2e 整合測試 (2026-08-04 C2)。

驗證 run_merge_folder_batch_service 完整 pipeline:
1. Stage 1: zh_cn → zh_tw 翻譯
2. Stage 2: _extracted → assets/ 合併
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services_impl.pipelines.merge_service import run_merge_folder_batch_service


class TestMergePipelineE2E:
    """Stage 1 + Stage 2 整合測試。"""

    def test_full_pipeline_stage1_then_stage2(self, tmp_path: Path):
        """e2e: fake _extracted + assets/, 確認 stage 2 正確生成 assets/zh_tw.json。"""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # 建立 _extracted 結構
        extracted = input_dir / "ae2ct_extracted" / "ae2ct" / "lang"
        extracted.mkdir(parents=True)
        (extracted / "zh_cn.json").write_text(
            json.dumps({"item.ae2ct.certus_quartz": "赛特斯石英"}), encoding="utf-8"
        )
        (extracted / "en_us.json").write_text(
            json.dumps({"item.ae2ct.certus_quartz": "Certus Quartz"}), encoding="utf-8"
        )

        session = MagicMock()
        session.progress = 1.0
        del session.snapshot
        # 補足 session 所需方法，避免 daemon thread 拋出 UnhandledThreadException
        session.progress = 1.0
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
        assert last["progress"] == 1.0
        assert not last.get("error", False)

        # 驗證 Stage 1 輸出
        summary = last.get("summary", {})
        assert summary["success_folders"] == 1
        assert summary["failed_folders"] == 0

        # Stage 1 應生成 lang_output 檔案
        lang_output = output_dir / "lang_output"
        assert lang_output.exists()

    def test_pipeline_with_multiple_mods(self, tmp_path: Path):
        """e2e: 多個 mod, 確認各自獨立合併。"""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        for mod in ("ae2ct", "aether", "ars"):
            extracted = input_dir / f"{mod}_extracted" / mod / "lang"
            extracted.mkdir(parents=True)
            (extracted / "zh_cn.json").write_text(
                json.dumps({f"key.{mod}": f"中文_{mod}"}), encoding="utf-8"
            )

        session = MagicMock()
        session.progress = 1.0
        del session.snapshot
        # 補足 session 所需方法，避免 daemon thread 拋出 UnhandledThreadException
        session.progress = 1.0
        results = list(
            run_merge_folder_batch_service(
                input_dir=str(input_dir),
                output_dir=str(output_dir),
                session=session,
                only_process_lang=True,
                process_zh_cn=True,
                patchouli_skip=True,
            )
        )

        last = results[-1]
        assert not last.get("error", False)

        # 三個 mod 都應有 assets/zh_tw.json
        for mod in ("ae2ct", "aether", "ars"):
            assets_tw = output_dir / "lang_output" / "assets" / mod / "lang" / "zh_tw.json"
            assert assets_tw.exists(), f"Missing {assets_tw}"

    def test_pipeline_skips_stage2_when_enable_extracted_merge_false(self, tmp_path: Path, monkeypatch):
        """e2e: enable_extracted_to_assets_merge=False 時不跑 Stage 2。"""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        extracted = input_dir / "test_extracted" / "testmod" / "lang"
        extracted.mkdir(parents=True)
        (extracted / "zh_cn.json").write_text('{"k1":"中文"}', encoding="utf-8")

        # Mock load_config to disable Stage 2
        from translation_tool.utils import config_manager
        monkeypatch.setattr(
            config_manager, "load_config",
            lambda *args, **kwargs: {"lang_merger": {"enable_extracted_to_assets_merge": False}}
        )

        session = MagicMock()
        session.progress = 1.0
        del session.snapshot
        # 補足 session 所需方法，避免 daemon thread 拋出 UnhandledThreadException
        session.progress = 1.0
        results = list(
            run_merge_folder_batch_service(
                input_dir=str(input_dir),
                output_dir=str(output_dir),
                session=session,
                only_process_lang=True,
            )
        )

        # Stage 2 不應生成 assets/
        assets_dir = output_dir / "lang_output" / "assets"
        assert not assets_dir.exists(), f"Stage 2 should be skipped, but {assets_dir} exists"
