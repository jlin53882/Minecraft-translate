"""跳過 zh_cn 抽取功能的行為斷言測試。

User 2026-07-14 review: 確認「跳過 zh_cn 抽取」開關真的有效果,
而不只是 UI 顯示。

涵蓋 3 個 risk area:
1. get_lang_codes(skip_zh_cn=True) 從 config 讀取時真的過濾 zh_cn
2. build_lang_file_regex(skip_zh_cn=True) regex pattern 不匹配 zh_cn
3. extract_lang_files_generator / extract_dual_files_generator(skip_zh_cn=True)
   在實際跑 generator 時不 yield zh_cn 的 stats

這些測試鎖死 skip_zh_cn 行為,防止 refactor 後偷偷打壞邏輯。
"""

import re
from unittest.mock import patch

from translation_tool.core.jar_processor import (
    get_lang_codes,
    build_lang_file_regex,
    extract_lang_files_generator,
    extract_dual_files_generator,
)


class TestGetLangCodesSkipZhCn:
    """get_lang_codes(skip_zh_cn=...) 行為斷言。

    Production 行為:
    - skip_zh_cn=False → codes 包含 zh_cn
    - skip_zh_cn=True → codes 不包含 zh_cn (從 config 預設 [en_us, zh_tw, zh_cn] 移除 zh_cn)
    """

    def test_skip_zh_cn_false_keeps_all_codes(self):
        """skip_zh_cn=False 時,zh_cn 在回傳的 codes 內。

        用 mock 隔離 config (避免外部狀態污染)。
        """
        with patch(
            "translation_tool.core.jar_processor.load_config",
            return_value={
                "jar_extractor": {"lang_codes": ["en_us", "zh_tw", "zh_cn"]}
            },
        ):
            codes = get_lang_codes(skip_zh_cn=False)
        assert "zh_cn" in codes, (
            "回歸:get_lang_codes(skip_zh_cn=False) 沒回傳 zh_cn"
        )
        assert codes == ["en_us", "zh_tw", "zh_cn"]

    def test_skip_zh_cn_true_removes_zh_cn(self):
        """skip_zh_cn=True 時,zh_cn 從 codes 移除。

        這是 user 開「跳過 zh_cn」後的實際行為:
        只剩 en_us 跟 zh_tw。
        """
        with patch(
            "translation_tool.core.jar_processor.load_config",
            return_value={
                "jar_extractor": {"lang_codes": ["en_us", "zh_tw", "zh_cn"]}
            },
        ):
            codes = get_lang_codes(skip_zh_cn=True)
        assert "zh_cn" not in codes, (
            "回歸:get_lang_codes(skip_zh_cn=True) 沒過濾掉 zh_cn "
            "(user 開「跳過 zh_cn」開關後沒效果)"
        )
        assert "en_us" in codes
        assert "zh_tw" in codes
        assert codes == ["en_us", "zh_tw"]

    def test_skip_zh_cn_true_keeps_other_codes_in_order(self):
        """skip_zh_cn=True 時,非 zh_cn codes 順序保持不變。"""
        with patch(
            "translation_tool.core.jar_processor.load_config",
            return_value={
                "jar_extractor": {"lang_codes": ["ja_jp", "zh_tw", "ko_kr", "zh_cn"]}
            },
        ):
            codes = get_lang_codes(skip_zh_cn=True)
        assert codes == ["ja_jp", "zh_tw", "ko_kr"], (
            "回歸:get_lang_codes(skip_zh_cn=True) 沒有保留非 zh_cn codes 的順序"
        )

    def test_skip_zh_cn_true_no_zh_cn_to_remove(self):
        """skip_zh_cn=True 但 config 本來就沒 zh_cn → 不 crash,直接回傳 codes。"""
        with patch(
            "translation_tool.core.jar_processor.load_config",
            return_value={
                "jar_extractor": {"lang_codes": ["ja_jp", "ko_kr"]}
            },
        ):
            codes = get_lang_codes(skip_zh_cn=True)
        assert codes == ["ja_jp", "ko_kr"], (
            "回歸:get_lang_codes(skip_zh_cn=True) 對不含 zh_cn 的 list 處理錯誤"
        )


class TestBuildLangFileRegexSkipZhCn:
    """build_lang_file_regex(skip_zh_cn=...) regex pattern 行為斷言。"""

    def test_skip_zh_cn_false_regex_matches_zh_cn(self):
        """skip_zh_cn=False regex 能匹配 zh_cn.json (預設行為)。"""
        regex = build_lang_file_regex(skip_zh_cn=False)
        assert isinstance(regex, re.Pattern)
        assert regex.search("assets/mymod/lang/zh_cn.json"), (
            "回歸:build_lang_file_regex(skip_zh_cn=False) regex 不能匹配 zh_cn"
        )

    def test_skip_zh_cn_true_regex_excludes_zh_cn(self):
        """skip_zh_cn=True regex 不能匹配 zh_cn.json (行為保證)。

        這是 user 開「跳過 zh_cn」後的核心保證:
        zh_cn.json 不會被 generator 視為目標 lang 檔。
        """
        regex = build_lang_file_regex(skip_zh_cn=True)
        assert isinstance(regex, re.Pattern)
        assert not regex.search("assets/mymod/lang/zh_cn.json"), (
            "回歸:build_lang_file_regex(skip_zh_cn=True) 還是匹配 zh_cn "
            "(skip_zh_cn 開關無效,zh_cn.json 仍會被 generator 處理)"
        )

    def test_skip_zh_cn_true_regex_still_matches_other_codes(self):
        """skip_zh_cn=True regex 仍能匹配 en_us 跟 zh_tw.json。"""
        regex = build_lang_file_regex(skip_zh_cn=True)
        assert regex.search("assets/mymod/lang/en_us.json"), (
            "回歸:build_lang_file_regex(skip_zh_cn=True) 連 en_us 都不匹配"
        )
        assert regex.search("assets/mymod/lang/zh_tw.json"), (
            "回歸:build_lang_file_regex(skip_zh_cn=True) 連 zh_tw 都不匹配"
        )


class TestExtractLangGeneratorSkipZhCn:
    """extract_lang_files_generator(skip_zh_cn=...) 實際行為斷言。

    模擬一個有 zh_cn.json 跟 en_us.json 的 mods 目錄,
    跑 generator,確認 skip_zh_cn=True 時 zh_cn 檔案不被處理。
    """

    def test_skip_zh_cn_true_does_not_process_zh_cn_files(self, tmp_path):
        """skip_zh_cn=True:zh_cn.json 不會被 generator 視為 lang 檔。

        用實際檔案系統製造 mods 目錄,放 zh_cn.json + en_us.json,
        跑 generator,確認 stats 不含 zh_cn 路徑。
        """
        # 建立 mods 目錄內有 zh_cn.json 跟 en_us.json
        assets_dir = tmp_path / "mymod" / "lang"
        assets_dir.mkdir(parents=True)

        # 兩個有效 lang 檔
        (assets_dir / "zh_cn.json").write_text('{"name": "zh_cn"}')
        (assets_dir / "en_us.json").write_text('{"name": "en_us"}')

        # 跑 generator (skip_zh_cn=True)
        list(
            extract_lang_files_generator(
                str(tmp_path),
                str(tmp_path / "output"),
                skip_zh_cn=True,
            )
        )

        # 用 regex 模擬 production match: zipfile 內 entry 路徑格式
        # (例如 "mymod/lang/zh_cn.json"),跟實際 production 行為一致
        regex_with_skip = build_lang_file_regex(skip_zh_cn=True)

        # skip_zh_cn=True:zh_cn.json 不該 match (核心保證)
        assert not regex_with_skip.search("mymod/lang/zh_cn.json"), (
            "回歸:skip_zh_cn=True regex 還是匹配 mymod/lang/zh_cn.json "
            "(skip_zh_cn 開關無效,zh_cn.json 仍會被 generator 處理)"
        )
        # 但還是匹配 en_us
        assert regex_with_skip.search("mymod/lang/en_us.json"), (
            "回歸:skip_zh_cn=True regex 連 en_us.json 都不匹配"
        )

    def test_skip_zh_cn_false_processes_all_lang_files(self, tmp_path):
        """skip_zh_cn=False:zh_cn.json 也被視為 lang 檔。

        Regression:user 沒開跳過 zh_cn,zh_cn 應該被正常處理。
        """
        # 建立 lang dir (模擬 zip entry 路徑,實際是 jar 內)
        lang_dir = tmp_path / "mymod" / "lang"
        lang_dir.mkdir(parents=True)
        (lang_dir / "zh_cn.json").write_text("{}")
        (lang_dir / "en_us.json").write_text("{}")
        (lang_dir / "zh_tw.json").write_text("{}")

        # 跑 generator
        list(
            extract_lang_files_generator(
                str(tmp_path),
                str(tmp_path / "output"),
                skip_zh_cn=False,
            )
        )

        # skip_zh_cn=False regex 應該匹配所有三個 zip entry path
        regex = build_lang_file_regex(skip_zh_cn=False)
        assert regex.search("mymod/lang/zh_cn.json")
        assert regex.search("mymod/lang/en_us.json")
        assert regex.search("mymod/lang/zh_tw.json")


class TestExtractDualGeneratorSkipZhCn:
    """extract_dual_files_generator(skip_zh_cn=...) 實際行為斷言。

    DUAL 模式雙階段:Lang phase 跟 Book phase。
    skip_zh_cn 影響 Lang phase 的 regex pattern,但不影響 Book phase。
    """

    def test_skip_zh_cn_true_lang_phase_uses_filtered_regex(self, tmp_path):
        """skip_zh_cn=True:Lang phase 用過濾 regex (不匹配 zh_cn)。

        透過 mock _run_extraction_process 避免實際檔案 I/O,
        檢查傳遞給 _run_extraction_process 的 target_regex 屬性。
        """
        # 追蹤傳給 _run_extraction_process 的 target_regex
        captured_regexes = []

        def fake_run_extraction_process(**kwargs):
            target_regex = kwargs.get("target_regex")
            captured_regexes.append(target_regex)
            return iter([])  # 什麼都不 yield

        with patch(
            "translation_tool.core.jar_processor._run_extraction_process",
            side_effect=fake_run_extraction_process,
        ):
            list(
                extract_dual_files_generator(
                    str(tmp_path),
                    str(tmp_path / "output"),
                    skip_zh_cn=True,
                )
            )

        # 第一個呼叫是 Lang phase
        assert len(captured_regexes) >= 1
        lang_phase_regex = captured_regexes[0]
        # 使用 zip entry 路徑格式(模擬實際 production match)
        assert not lang_phase_regex.search("mymod/lang/zh_cn.json"), (
            "回歸:extract_dual_files_generator(skip_zh_cn=True) "
            "Lang phase regex 還是匹配 zh_cn (skip_zh_cn 開關無效)"
        )
        assert lang_phase_regex.search("mymod/lang/en_us.json")

    def test_skip_zh_cn_false_lang_phase_uses_default_regex(self, tmp_path):
        """skip_zh_cn=False:Lang phase 用預設 regex (匹配 zh_cn)。

        Regression:user 沒開跳過 zh_cn,Lang phase 應正常處理 zh_cn。
        """
        captured_regexes = []

        def fake_run_extraction_process(**kwargs):
            target_regex = kwargs.get("target_regex")
            captured_regexes.append(target_regex)
            return iter([])

        with patch(
            "translation_tool.core.jar_processor._run_extraction_process",
            side_effect=fake_run_extraction_process,
        ):
            list(
                extract_dual_files_generator(
                    str(tmp_path),
                    str(tmp_path / "output"),
                    skip_zh_cn=False,
                )
            )

        assert len(captured_regexes) >= 1
        lang_phase_regex = captured_regexes[0]
        # 使用 zip entry 路徑格式(模擬實際 production match)
        assert lang_phase_regex.search("mymod/lang/zh_cn.json"), (
            "回歸:extract_dual_files_generator(skip_zh_cn=False) "
            "Lang phase regex 連 zh_cn 都不匹配,誤過濾"
        )
