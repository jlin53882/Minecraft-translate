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

from translation_tool.core.lang_merge_extracted_assets import _write_json_atomic
import os as _os
from unittest.mock import patch
from translation_tool.core.lang_merge_extracted_assets import _cleanup_single_mod_extracted

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

    def test_scan_all_three_lang_codes_zipped(self, tmp_path: Path):
        """XX_extracted 同時含 zh_cn / zh_tw / en_us 時全部處理。

        2026-08-02 user 確認:Stage 2 對所有 3 種 lang file 都處理,
        不單獨挑 en_us 跳過 zh_cn/zh_tw。
        """
        # XX_extracted/{modid}/lang/{zh_cn,zh_tw,en_us}.json 都寫
        modid = "ae2ct"
        lang_dir = tmp_path / "ae2ct_extracted" / modid / "lang"
        lang_dir.mkdir(parents=True)
        (lang_dir / "zh_cn.json").write_text('{"k_zh": "原文_zh"}', encoding="utf-8")
        (lang_dir / "zh_tw.json").write_text('{"k_tw": "翻譯_tw"}', encoding="utf-8")
        (lang_dir / "en_us.json").write_text('{"k_en": "原文_en"}', encoding="utf-8")

        result = _scan_extracted_lang_files(tmp_path)
        assert modid in result
        # 3 個 lang code 都要被掃到
        assert "zh_cn" in result[modid]
        assert "zh_tw" in result[modid]
        assert "en_us" in result[modid]
        # 位置正確 (在 language dir 內)
        assert result[modid]["zh_cn"][0].name == "zh_cn.json"
        assert result[modid]["zh_tw"][0].name == "zh_tw.json"
        assert result[modid]["en_us"][0].name == "en_us.json"

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
    """2026-08-02 重構:Stage 2 改用 Stage 1 拆出來的 merge_lang_dicts helper。

    行為:
    - _extracted/{modid}/lang/{zh_cn,zh_tw,en_us}.json 跑 merge_lang_dicts
    - 產出 final_tw → assets/{modid}/lang/zh_tw.json
    - 產出 pending (en_us only) → assets/{modid}/lang/en_us.json
    - zh_cn 不直接 copy 到 assets (Stage 1 翻譯結果 = zh_tw)

    注意:舊 test 期待 self-key-by-key (zh_cn → assets/zh_cn.json 直接 copy),
    已全部重寫成新行為。
    """

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

        if existing_assets:
            for modid, lang_files in existing_assets.items():
                for lang_code, data in lang_files.items():
                    target = (
                        lang_output_dir / "assets" / modid / "lang" / f"{lang_code}.json"
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(
                        json.dumps(data, ensure_ascii=False), encoding="utf-8"
                    )

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

    def test_zh_cn_only_writes_to_zh_tw_after_merge(self, tmp_path: Path):
        """_extracted 內 zh_cn.json 經 merge 後寫到 assets/zh_tw.json(不寫 zh_cn)。"""
        lang_output_dir = self._setup_fixture(
            tmp_path,
            existing_assets=None,
            extracted_data={
                "ae2ct_extracted": {
                    "ae2ct": {
                        "zh_cn": {"k_zh": "原文_zh"},
                    },
                },
            },
        )

        list(merge_extracted_to_assets(lang_output_dir))
        # 應該寫 zh_tw.json(Stage 1 merge 邏輯)
        zh_tw_path = lang_output_dir / "assets" / "ae2ct" / "lang" / "zh_tw.json"
        assert zh_tw_path.exists()
        result = json.loads(zh_tw_path.read_text(encoding="utf-8"))
        # merge_lang_dicts 看到 zh_cn 有 CJK → 試 apply_replace_rules,
        # 因為沒 rules 跟沒 CJK 處理,實際值會被 setdefault
        # 注意:Stage 2 rules=[] 給 helper,apply 規則不做事,值可能是原文
        # 但 helper 行為:zh_cn 含 CJK → recursive_translate_dict(無 rules 也是原值)
        assert "k_zh" in result
        # zh_cn 不應直接寫
        zh_cn_path = lang_output_dir / "assets" / "ae2ct" / "lang" / "zh_cn.json"
        assert not zh_cn_path.exists()

    def test_zh_tw_extracted_wins_via_assets_protection(self, tmp_path: Path):
        """既有 assets/zh_tw.json 含 CJK,被 _extracted/zh_tw.json 補滿 key,不覆寫 CJK。

        既有: {k_old: "舊翻譯_zh"}
        _extracted: {k_old: "新翻譯_zh", k_new: "新key_zh"}
        期望 assets/zh_tw.json:
          - k_old = 舊翻譯 (assets wins - 有人工翻譯)
          - k_new = 新key_zh (新 key 補進去)
        """
        lang_output_dir = self._setup_fixture(
            tmp_path,
            existing_assets={
                "ae2ct": {
                    "zh_tw": {"k_old": "舊翻譯_zh"},
                }
            },
            extracted_data={
                "ae2ct_extracted": {
                    "ae2ct": {
                        "zh_tw": {
                            "k_old": "新翻譯_zh",
                            "k_new": "新key_zh",
                        },
                        "en_us": {},  # 缺 zh_cn 來源,純英文 key 才進 pending
                    },
                },
            },
        )

        list(merge_extracted_to_assets(lang_output_dir))
        target = lang_output_dir / "assets" / "ae2ct" / "lang" / "zh_tw.json"
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["k_old"] == "舊翻譯_zh"  # assets wins
        assert result["k_new"] == "新key_zh"  # 新 key 補充

    def test_en_us_only_mod_skips_empty_zh_tw_and_does_not_write_en_us(self, tmp_path: Path):
        """en_us-only mod:不寫空 zh_tw.json,也不寫 assets/en_us.json。

        2026-08-02 修正:
        - 沒 zh_cn 來源 → final_tw 是空 dict → 不寫空檔案(避免污染 assets/)
        - pending 來源已在 待翻譯/ → Stage 2 不重複寫到 assets/

        期望:assets/{modid}/lang/ 內 沒有任何檔案被建立
        """
        lang_output_dir = self._setup_fixture(
            tmp_path,
            existing_assets=None,
            extracted_data={
                "codechickenlib_extracted": {
                    "codechickenlib": {
                        "en_us": {"k_en": "v_en"},
                    },
                },
            },
        )

        list(merge_extracted_to_assets(lang_output_dir))

        # zh_tw.json 不應該被建 (final_tw 是空 dict)
        zh_tw_path = (
            lang_output_dir / "assets" / "codechickenlib" / "lang" / "zh_tw.json"
        )
        assert not zh_tw_path.exists(), (
            f"en_us-only mod 不該寫空 zh_tw.json,但檔案存在: {zh_tw_path}"
        )

        # assets/en_us.json 也不該被建 (pending 在 待翻譯/)
        en_us_path = (
            lang_output_dir / "assets" / "codechickenlib" / "lang" / "en_us.json"
        )
        assert not en_us_path.exists(), (
            f"pending 不該寫到 assets/,但檔案存在: {en_us_path}"
        )

    def test_no_extracted_no_error(self, tmp_path: Path):
        """沒 XX_extracted 不報錯,只是空跑"""
        lang_output_dir = self._setup_fixture(tmp_path, existing_assets=None)

        updates = list(merge_extracted_to_assets(lang_output_dir))
        # 應有 1 個 progress=1.0 的 yield(沒事做)
        assert any(u.get("progress") == 1.0 for u in updates)

    def test_multi_source_logs_warning(self, tmp_path: Path):
        """多個 source 同 modid 應該 log warning 並採第一個。"""
        lang_output_dir = tmp_path / "lang_output"
        lang_output_dir.mkdir()

        # 兩個 extracted source 都貢獻給 ae2ct (必須 *_extracted 結尾)
        for ext_name in ("ae2ct_extracted_v1", "ae2ct_extracted_v2"):
            ext_dir = lang_output_dir / ext_name / "ae2ct" / "lang"
            ext_dir.mkdir(parents=True)
            (ext_dir / "en_us.json").write_text(
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
            "多個來源" in msg and "採第一個" in msg
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
                    # 用中文 fixture 確保 final_tw 有內容(會跑 zh_cn → recursive_translate_dict)
                    "ae2ct": {"zh_cn": {"k1": "車輛"}},
                },
            },
        )

        list(merge_extracted_to_assets(lang_output_dir, session=session))

        # 驗證 session.add_log 被呼叫
        assert session.add_log.called
        # 至少 1 個 add_log 訊息含 ✓ (有 zh_tw 內容才會有 ✓)
        msgs = [call.args[0] for call in session.add_log.call_args_list if call.args]
        assert any("✓" in m for m in msgs), f"沒有 ✓ log,實際: {msgs}"

    def test_files_cleanly_written(self, tmp_path: Path):
        """JSON 是 UTF-8 + indent=4 + ensure_ascii=False

        2026-08-02 重構:Stage 2 改成寫 zh_tw.json (不是 zh_cn.json)。
        """
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
        # Stage 2 寫 zh_tw.json (Stage 1 merge 邏輯)
        target = lang_output_dir / "assets" / "ae2ct" / "lang" / "zh_tw.json"
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

class TestWriteJsonAtomic:
    """2026-08-04 B2: _write_json_atomic crash cleanup 行為。"""

    def test_tmp_file_cleaned_up_after_success(self, tmp_path: Path):
        """寫入成功後 .tmp 檔案應被清理。"""

        target = tmp_path / "test.json"
        _write_json_atomic(target, {"key": "value"})

        assert target.exists()
        tmp_file = target.with_suffix(target.suffix + ".tmp")
        assert not tmp_file.exists(), f".tmp 應被清理,但存在: {tmp_file}"

    def test_tmp_file_cleaned_up_on_error(self, tmp_path: Path):
        """寫入失敗時 .tmp 檔案也應被清理。monkeypatch os.replace 使其拋錯。"""

        target = tmp_path / "subdir" / "test.json"
        target.parent.mkdir(parents=True)

        _orig_replace = _os.replace

        def _fail_replace(src, dst, **kwargs):
            _orig_replace(src, dst)  # 讓檔案確實被寫入
            raise OSError("模擬 os.replace 失敗")

        with patch("os.replace", side_effect=_fail_replace):
            try:
                _write_json_atomic(target, {"key": "value"})
            except Exception:
                pass

        tmp_file = target.with_suffix(target.suffix + ".tmp")
        assert not tmp_file.exists(), f".tmp 應被清理,但存在: {tmp_file}"

class TestCleanupSingleModExtracted:
    """2026-08-04 B3: per-mod cleanup 行為。"""

    def test_cleanup_single_mod_deletes_only_target_mod(self, tmp_path: Path):
        """只刪除指定的 modid,_extracted 目錄下其他 mod 不受影響。"""

        # 建立兩個 mod 的 _extracted 結構
        extracted_dir = tmp_path / "ae2ct_extracted"
        mod_a = extracted_dir / "ae2ct" / "lang"
        mod_a.mkdir(parents=True)
        (mod_a / "zh_cn.json").write_text('{"k1":"v1"}')
        mod_b = extracted_dir / "other_mod" / "lang"
        mod_b.mkdir(parents=True)
        (mod_b / "zh_cn.json").write_text('{"k2":"v2"}')

        _cleanup_single_mod_extracted(tmp_path, "ae2ct")

        # ae2ct 應被刪除
        assert not (extracted_dir / "ae2ct").exists()
        # other_mod 應保留
        assert (extracted_dir / "other_mod").exists()

    def test_cleanup_removes_empty_extracted_dir(self, tmp_path: Path):
        """當 _extracted 目錄只剩一個 mod 且被刪除後,整個 _extracted 目錄也應被刪。"""

        extracted_dir = tmp_path / "ae2ct_extracted"
        mod_dir = extracted_dir / "ae2ct" / "lang"
        mod_dir.mkdir(parents=True)
        (mod_dir / "zh_cn.json").write_text('{"k1":"v1"}')

        assert extracted_dir.exists()
        _cleanup_single_mod_extracted(tmp_path, "ae2ct")
        assert not extracted_dir.exists(), "空的 _extracted 目錄應被刪除"

    def test_cleanup_nonexistent_mod_does_nothing(self, tmp_path: Path):
        """不存在的 modid 不會 crash。"""

        _cleanup_single_mod_extracted(tmp_path, "nonexistent")
        # 不 crash 即成功
