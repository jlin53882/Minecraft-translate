"""語系合併輸出的格式檢查：譯文與原文特殊字元不一致的條目只回報、不改動輸出。"""

from __future__ import annotations

import json
from unittest.mock import patch

from translation_tool.core import lang_merge_quality
from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_folder
from translation_tool.translation_db.quality import format_mismatches


def test_format_mismatches_skips_strings_without_any_format_characters():
    """沒有任何特殊字元的字串不做完整分析；有差異的才回傳。"""
    sources = {"a": "Plain", "b": "§6Gold %s", "c": "Count {0}", "d": "Fine"}
    translations = {"a": "純文字", "b": "黃金", "c": "{數量}", "d": "不錯", "e": "多餘"}

    found = format_mismatches(sources, translations)

    assert set(found) == {"b", "c"}
    with patch(
        "translation_tool.translation_db.quality.translation_issues"
    ) as analysed:
        analysed.return_value = []
        format_mismatches({"a": "Plain", "d": "Fine"}, {"a": "純文字", "d": "不錯"})
        analysed.assert_not_called()


def test_warn_format_mismatches_logs_count_and_samples_and_keeps_output():
    final = {"a": "黃金", "b": "§6黃金%s", "c": "ok"}
    en = {"a": "§6Gold %s", "b": "§6Gold %s", "c": "ok"}
    with patch.object(lang_merge_quality, "log_warning") as warn:
        count = lang_merge_quality.warn_format_mismatches(final, en, "[mod] ")

    assert count == 1
    message = warn.call_args.args[0]
    assert "1 筆" in message and "a" in message and "[mod] " in message
    assert final["a"] == "黃金"  # 只回報，不改動

    with patch.object(lang_merge_quality, "log_warning") as warn:
        assert lang_merge_quality.warn_format_mismatches(None, None, "x") == 0
        warn.assert_not_called()


def test_folder_merge_keeps_author_translation_but_warns_about_format(tmp_path):
    """模組作者自帶的 zh_tw 少了色碼：照常輸出，合併日誌警告。"""
    lang = tmp_path / "in" / "assets" / "foo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(
        json.dumps({"item.gold": "§6Gold %s ingot", "item.ok": "Fine"}),
        encoding="utf-8",
    )
    (lang / "zh_tw.json").write_text(
        json.dumps({"item.gold": "黃金錠", "item.ok": "還行"}, ensure_ascii=False),
        encoding="utf-8",
    )
    out = tmp_path / "out"

    with patch.object(lang_merge_quality, "log_warning") as warn:
        list(
            merge_zhcn_to_zhtw_from_folder(
                str(tmp_path / "in"), str(out), only_process_lang=True
            )
        )

    written = next((out / "lang_output").rglob("zh_tw.json"))
    assert json.loads(written.read_text(encoding="utf-8"))["item.gold"] == "黃金錠"
    assert any("特殊字元與原文不一致" in call.args[0] for call in warn.call_args_list)
