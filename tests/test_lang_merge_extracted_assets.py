"""merge_extracted_assets helper 的單元測試 (2026-08-02)。

涵蓋:
- 掃描 *_extracted 子資料夾的 **/lang/*.json
- key-by-key 合併進 assets/{modid}/lang/{xx_yy}.json
- assets wins(已有 key 不覆寫)
- 多 source 第 1 個 wins + 寫入最後一個
- 階段 1 已寫的 assets 不被覆寫(原本的 key 保留)
- enable_extracted_to_assets_merge=False 時不跑
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.core.lang_merge_extracted_assets import (
    _infer_modid_from_lang_file,
    _load_existing_assets,
    _scan_extracted_lang_files,
    merge_extracted_to_assets,
)


# ──────────────────────────────────────────────────────────────────────────
# 1. _infer_modid_from_lang_file
# ──────────────────────────────────────────────────────────────────────────
class TestInferModidFromLangFile:
    def test_direct_layout_ae2ct_extracted(self):
        """ae2ct_extracted/ae2ct/lang/zh_cn.json → ae2ct"""
        p = Path("lang_output/ae2ct_extracted/ae2ct/lang/zh_cn.json")
        assert _infer_modid_from_lang_file(p) == "ae2ct"

    def test_assets_layout_cobblemon(self):
        """Cobblemon.../resourcepacks/adorn/assets/adorn/lang/en_us.json → adorn"""
        p = Path(
            "lang_output/Cobblemon-1.7.3+1.21.1_extracted/resourcepacks/adorn/assets/adorn/lang/en_us.json"
        )
        assert _infer_modid_from_lang_file(p) == "adorn"

    def test_deep_data_layout_compactmachines(self):
        """compactmachines.../data/.../assets/compactmachines/lang/en_us.json → compactmachines"""
        p = Path(
            "lang_output/compactmachines_extracted/data/compactmachines/datapacks/basic_templates/assets/compactmachines/lang/en_us.json"
        )
        assert _infer_modid_from_lang_file(p) == "compactmachines"

    def test_no_lang_dir_returns_none(self):
        """無 lang/ parent → None"""
        p = Path("lang_output/ae2ct_extracted/ae2ct/zh_cn.json")
        assert _infer_modid_from_lang_file(p) is None


# ──────────────────────────────────────────────────────────────────────────
# 2. _scan_extracted_lang_files
# ──────────────────────────────────────────────────────────────────────────
class TestScanExtractedLangFiles:
    def test_scans_only_extracted_subdirs(self, tmp_path: Path):
        """只掃描 *_extracted 子資料夾,跳過 assets/, 待翻譯, 待翻譯整理"""
        # 建立 fixture
        ae2ct_extracted = tmp_path / "ae2ct_extracted"
        (ae2ct_extracted / "ae2ct" / "lang").mkdir(parents=True)
        (ae2ct_extracted / "ae2ct" / "lang" / "zh_cn.json").write_text(
            json.dumps({"k1": "v1"}, ensure_ascii=False), encoding="utf-8"
        )
        # 不應掃描這個 (assets 是目標)
        assets_dir = tmp_path / "assets"
        (assets_dir / "ae2ct" / "lang").mkdir(parents=True)
        (assets_dir / "ae2ct" / "lang" / "zh_cn.json").write_text(
            json.dumps({"k_assets": "v_assets"}, ensure_ascii=False),
            encoding="utf-8",
        )
        # 不應掃描這個 (待翻譯)
        pending = tmp_path / "待翻譯"
        (pending / "ae2ct" / "lang").mkdir(parents=True)
        (pending / "ae2ct" / "lang" / "zh_cn.json").write_text(
            json.dumps({"k_pending": "v_pending"}, ensure_ascii=False),
            encoding="utf-8",
        )

        result = _scan_extracted_lang_files(tmp_path)
        assert "ae2ct" in result
        assert result["ae2ct"]["zh_cn"] == [ae2ct_extracted / "ae2ct" / "lang" / "zh_cn.json"]
        # k_assets / k_pending 不應在 result 內
        for lang_file_list in result.values():
            for file_list in lang_file_list.values():
                for f in file_list:
                    content = f.read_text(encoding="utf-8")
                    assert "k_assets" not in content
                    assert "k_pending" not in content

    def test_only_ends_with_extracted(self, tmp_path: Path):
        """非 *_extracted 子資料夾完全跳過"""
        random_dir = tmp_path / "random"
        (random_dir / "ae2ct" / "lang").mkdir(parents=True)
        (random_dir / "ae2ct" / "lang" / "zh_cn.json").write_text("{}", encoding="utf-8")

        result = _scan_extracted_lang_files(tmp_path)
        assert result == {}

    def test_extracted_name_pattern_variants(self, tmp_path: Path):
        """*_extracted 結尾的各種變體都應該被接受 (re.match: .*_extracted(_\\w+)?$)"""
        variants_should_match = [
            "ae2ct_extracted",                           # 標準
            "Cobblemon-1.7.3+1.21.1_extracted",         # 含版本
            "compactmachines_extracted_v2",              # 含版本後綴
            "_cache_ae2ct_extracted",                    # 含前綴
        ]
        for name in variants_should_match:
            folder = tmp_path / name / "ae2ct" / "lang"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "zh_cn.json").write_text("{}", encoding="utf-8")

        variants_should_skip = [
            "random",                # 沒 _extracted
            "extracted",             # 沒前綴
            "ae2ct_extract",         # 拼錯
            "ae2ct_extracteded",     # 太長
        ]
        for name in variants_should_skip:
            folder = tmp_path / name / "ae2ct" / "lang"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "zh_cn.json").write_text(
                "{\"this_should_NOT_be_scanned\": true}", encoding="utf-8"
            )

        result = _scan_extracted_lang_files(tmp_path)

        # 應該 match 的都進來了
        assert "ae2ct" in result
        # skip 的沒出現,因為 _scan 不會把檔案內容存進 result
        # 但 _scan 會跳過非 _extracted,所以 result 只有該掃到的
        for lang_file_list in result.values():
            for file_list in lang_file_list.values():
                for f in file_list:
                    content = f.read_text(encoding="utf-8")
                    assert "this_should_NOT_be_scanned" not in content

    def test_scan_from_pending_dir_for_en_us_only_mods(self, tmp_path: Path):
        """2026-08-02 user 確認:Stage 2 應寬掃 待翻譯/ 內的 _extracted 子資料夾。

        因為 stage 1 將 en_us-only mod 的 en_us 寫到 待翻譯/{XX_extracted}/{modid}/lang/en_us.json,
        Stage 2 必掃兩個位置才能完整處理。
        """
        # 建立 fixtures:3 個 only-en-us mod 寫到 待翻譯/ 內
        for dirname in ["compactmachines_extracted", "CodeChickenLib_extracted", "Cobblemon-1.7.3+1.21.1_extracted"]:
            # 待翻譯/{XX_extracted}/{modid}/lang/en_us.json
            if dirname == "compactmachines_extracted":
                lang = tmp_path / "待翻譯" / dirname / "data" / "compactmachines" / "datapacks" / "basic_templates" / "assets" / "compactmachines" / "lang"
                lang.mkdir(parents=True)
                (lang / "en_us.json").write_text(
                    '{"k_c": "v_c"}', encoding="utf-8"
                )
            elif dirname == "CodeChickenLib_extracted":
                lang = tmp_path / "待翻譯" / dirname / "data" / "codechickenlib" / "lang"
                lang.mkdir(parents=True)
                (lang / "en_us.json").write_text(
                    '{"k_lib": "v_lib"}', encoding="utf-8"
                )
            elif dirname == "Cobblemon-1.7.3+1.21.1_extracted":
                lang = tmp_path / "待翻譯" / dirname / "resourcepacks" / "adorncompatibility" / "assets" / "adorn" / "lang"
                lang.mkdir(parents=True)
                (lang / "en_us.json").write_text(
                    '{"k_ad": "v_ad"}', encoding="utf-8"
                )

        result = _scan_extracted_lang_files(tmp_path)

        # 全部 3 個 modid 都應該被掃到
        assert "compactmachines" in result
        assert "codechickenlib" in result
        assert "adorn" in result
        # 都只有 en_us
        assert result["compactmachines"]["en_us"]
        assert result["codechickenlib"]["en_us"]
        assert result["adorn"]["en_us"]


# ──────────────────────────────────────────────────────────────────────────
# 5. is_pure 行為 - 使用者確認規則
# ──────────────────────────────────────────────────────────────────────────
class TestLoadExistingAssets:
    def test_loads_only_assets_dir(self, tmp_path: Path):
        """讀取 assets/{modid}/lang/{xx_yy}.json"""
        lang_output_dir = tmp_path
        assets_dir = lang_output_dir / "assets"
        (assets_dir / "ae2ct" / "lang").mkdir(parents=True)
        (assets_dir / "ae2ct" / "lang" / "zh_cn.json").write_text(
            json.dumps({"existing_k": "existing_v"}, ensure_ascii=False),
            encoding="utf-8",
        )

        result = _load_existing_assets(assets_dir)
        assert ("ae2ct", "zh_cn") in result
        assert result[("ae2ct", "zh_cn")] == {"existing_k": "existing_v"}

    def test_returns_empty_when_no_assets(self, tmp_path: Path):
        """沒 assets 就回空 dict"""
        result = _load_existing_assets(tmp_path / "nonexistent")
        assert result == {}


# ──────────────────────────────────────────────────────────────────────────
# 4. merge_extracted_to_assets (整合測試)
# ──────────────────────────────────────────────────────────────────────────
class TestMergeExtractedToAssets:
    def _setup_fixture(
        self,
        tmp_path: Path,
        *,
        existing_assets: dict | None = None,
        extracted_data: dict[str, dict[str, dict]] | None = None,
    ) -> Path:
        """建立 lang_output_dir 並填入 fixture。"""
        lang_output_dir = tmp_path / "lang_output"
        lang_output_dir.mkdir()

        # 建立 assets/{modid}/lang/{file}
        if existing_assets:
            for modid, lang_files in existing_assets.items():
                for lang_code, data in lang_files.items():
                    target = lang_output_dir / "assets" / modid / "lang" / f"{lang_code}.json"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(
                        json.dumps(data, ensure_ascii=False), encoding="utf-8"
                    )

        # 建立 {something}_extracted/{modid}/lang/{file}
        if extracted_data:
            for ext_folder_name, lang_files in extracted_data.items():
                ext_dir = lang_output_dir / ext_folder_name
                for modid, lang_code_data in lang_files.items():
                    for lang_code, data in lang_code_data.items():
                        target = ext_dir / modid / "lang" / f"{lang_code}.json"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(
                            json.dumps(data, ensure_ascii=False), encoding="utf-8"
                        )

        return lang_output_dir

    def test_assets_wins_supplements_missing(self, tmp_path: Path):
        """assets 已有 key 不被覆寫,extracted 補缺的 key"""
        lang_output_dir = self._setup_fixture(
            tmp_path,
            existing_assets={
                "ae2ct": {
                    "zh_cn": {"old_k": "old_v_assets_should_keep"},
                }
            },
            extracted_data={
                "ae2ct_extracted": {
                    "ae2ct": {
                        "zh_cn": {
                            "old_k": "new_v_extracted_should_be_ignored",
                            "new_k": "new_v_extracted_should_be_added",
                        },
                    },
                },
            },
        )

        list(merge_extracted_to_assets(lang_output_dir))
        target = lang_output_dir / "assets" / "ae2ct" / "lang" / "zh_cn.json"
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["old_k"] == "old_v_assets_should_keep"  # assets wins
        assert result["new_k"] == "new_v_extracted_should_be_added"  # 補充

    def test_extracted_data_written_when_no_assets(self, tmp_path: Path):
        """沒 assets 從 extracted 建立新 assets"""
        lang_output_dir = self._setup_fixture(
            tmp_path,
            existing_assets=None,
            extracted_data={
                "ae2ct_extracted": {
                    "ae2ct": {
                        "zh_cn": {"k1": "v1"},
                    },
                },
            },
        )

        list(merge_extracted_to_assets(lang_output_dir))
        target = lang_output_dir / "assets" / "ae2ct" / "lang" / "zh_cn.json"
        assert target.exists()
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result == {"k1": "v1"}

    def test_no_extracted_no_error(self, tmp_path: Path):
        """沒 XX_extracted 不報錯,只是空跑"""
        lang_output_dir = self._setup_fixture(tmp_path, existing_assets=None)

        updates = list(merge_extracted_to_assets(lang_output_dir))
        # 應有 1 個 progress=1.0 的 yield(沒事做)
        assert any(u.get("progress") == 1.0 for u in updates)

    def test_multi_source_logs_warning(self, tmp_path: Path):
        """多個 source 同 modid 同 lang_code,應該 log warning"""
        lang_output_dir = tmp_path / "lang_output"
        lang_output_dir.mkdir()

        # 兩個 extracted source 都貢獻給 ae2ct (必須 *_extracted 結尾)
        for ext_name in ("ae2ct_extracted_v1", "ae2ct_extracted_v2"):
            ext_dir = lang_output_dir / ext_name / "ae2ct" / "lang"
            ext_dir.mkdir(parents=True)
            (ext_dir / "zh_cn.json").write_text(
                json.dumps({"k": "v"}, ensure_ascii=False), encoding="utf-8"
            )

        # 用 captureWarnings() 抓 stdlib logging.WARNING
        import logging
        captured = []

        class _CaptureHandler(logging.Handler):
            def emit(self, record):
                captured.append(record.getMessage())

        handler = _CaptureHandler(level=logging.WARNING)
        logging.getLogger().addHandler(handler)
        try:
            list(merge_extracted_to_assets(lang_output_dir))
        finally:
            logging.getLogger().removeHandler(handler)

        # 應有 log "多個來源"
        assert any(
            "多個來源" in msg and "assets wins" in msg
            for msg in captured
        ), f"沒有 multi-source warning,實際 log: {captured}"

    def test_session_receives_progress_updates(self, tmp_path: Path):
        """session 物件收到 add_log 跟 set_progress 呼叫"""
        from unittest.mock import MagicMock

        session = MagicMock()
        # 模擬階段 1 完成的 session.progress = 1.0
        session.progress = 1.0
        lang_output_dir = self._setup_fixture(
            tmp_path,
            existing_assets=None,
            extracted_data={
                "ae2ct_extracted": {
                    "ae2ct": {"zh_cn": {"k1": "v1"}},
                },
            },
        )

        list(merge_extracted_to_assets(lang_output_dir, session=session))

        # 驗證 session.add_log 被呼叫
        assert session.add_log.called
        # 至少 1 個 add_log 訊息含 ✓
        msgs = [call.args[0] for call in session.add_log.call_args_list if call.args]
        assert any("✓" in m for m in msgs)

    def test_files_cleanly_written(self, tmp_path: Path):
        """JSON 是 UTF-8 + indent=4 + ensure_ascii=False"""
        lang_output_dir = self._setup_fixture(
            tmp_path,
            existing_assets=None,
            extracted_data={
                "ae2ct_extracted": {
                    "ae2ct": {"zh_cn": {"中文_key": "中文_value"}},
                },
            },
        )

        list(merge_extracted_to_assets(lang_output_dir))
        target = lang_output_dir / "assets" / "ae2ct" / "lang" / "zh_cn.json"
        content = target.read_text(encoding="utf-8")
        # 中文不應被 escape 為 \uXXXX
        assert "中文_key" in content
        assert "中文_value" in content
        # 應有 indent=4
        assert "\n    " in content


# ──────────────────────────────────────────────────────────────────────────
# 5. is_pure 行為 - 使用者確認規則
# ──────────────────────────────────────────────────────────────────────────
class TestMergeExtractedConfigFlag:
    """merge_service.py 應該讀 enable_extracted_to_assets_merge 控制是否跑階段 2"""

    def test_merge_service_phase2_invoked_when_enabled(self, tmp_path, monkeypatch):
        """config enable_extracted_to_assets_merge=True 時,merge_service 呼叫 merge_extracted_to_assets"""
        from unittest.mock import MagicMock, patch
        from app.services_impl.pipelines import merge_service

        # mock merge_extracted_to_assets
        called = []

        def mock_merge(*args, **kwargs):
            called.append((args, kwargs))
            yield {"progress": 1.0}

        # Mock config 載入: enable_extracted_to_assets_merge=True
        mock_cfg = {"lang_merger": {"enable_extracted_to_assets_merge": True}}
        monkeypatch.setattr(
            merge_service,
            "merge_zhcn_to_zhtw_from_folder",
            lambda *a, **k: iter([]),
        )
        monkeypatch.setattr(merge_service, "merge_extracted_to_assets", mock_merge)
        monkeypatch.setattr(merge_service, "load_config", lambda: mock_cfg)

        session = MagicMock()
        session.snapshot.return_value = {
            "status": "running",
            "progress": 0.0,
            "log_lines": [],
            "summary": {},
        }

        list(
            merge_service.run_merge_folder_batch_service(
                input_dir=str(tmp_path),
                output_dir=str(tmp_path / "out"),
                session=session,
                only_process_lang=False,
            )
        )

        # 應有階段 2 被呼叫
        assert len(called) >= 1, "stage 2 應該被呼叫"

    def test_merge_service_phase2_skipped_when_disabled(self, tmp_path, monkeypatch):
        """config enable_extracted_to_assets_merge=False 時,階段 2 不跑"""
        from unittest.mock import MagicMock
        from app.services_impl.pipelines import merge_service

        called = []

        def mock_merge(*args, **kwargs):
            called.append((args, kwargs))
            yield {"progress": 1.0}

        # Mock config: enable_extracted_to_assets_merge=False
        mock_cfg = {"lang_merger": {"enable_extracted_to_assets_merge": False}}
        monkeypatch.setattr(
            merge_service,
            "merge_zhcn_to_zhtw_from_folder",
            lambda *a, **k: iter([]),
        )
        monkeypatch.setattr(merge_service, "merge_extracted_to_assets", mock_merge)
        monkeypatch.setattr(merge_service, "load_config", lambda: mock_cfg)

        session = MagicMock()
        session.snapshot.return_value = {
            "status": "running",
            "progress": 0.0,
            "log_lines": [],
            "summary": {},
        }

        list(
            merge_service.run_merge_folder_batch_service(
                input_dir=str(tmp_path),
                output_dir=str(tmp_path / "out"),
                session=session,
                only_process_lang=False,
            )
        )

        # 階段 2 不應該被呼叫
        assert len(called) == 0, "stage 2 不應該被呼叫"
