"""FTB Quests 清單欄位：沒送去翻譯的行（空行、圖片、換頁、引用）寫回時必須留在原位。"""

from __future__ import annotations

import json
from pathlib import Path

import ftb_snbt_lib as snbt
from ftb_snbt_lib.tag import List as SnbtList

from translation_tool.core import ftb_translator
from translation_tool.plugins.ftbquests import (
    ftbquests_lmtranslator,
    ftbquests_snbt_extractor,
    ftbquests_snbt_inject,
)

_DESC = ["Line one", "", "{image:ftbquests:x.png width:50}", "Line two", "{@pagebreak}"]


def _template(values: list[str]) -> SnbtList:
    return SnbtList([snbt.String(v) for v in values])


def test_merge_keeps_non_translatable_lines_in_place():
    merged = ftbquests_snbt_extractor.merge_translated_lines(
        _template(_DESC), ["譯一", "譯二"]
    )
    assert merged == [
        "譯一",
        "",
        "{image:ftbquests:x.png width:50}",
        "譯二",
        "{@pagebreak}",
    ]


def test_merge_handles_line_count_mismatch_without_losing_markers():
    template = _template(_DESC)
    more = ftbquests_snbt_extractor.merge_translated_lines(template, ["a", "b", "c"])
    assert more == [
        "a",
        "",
        "{image:ftbquests:x.png width:50}",
        "b",
        "c",
        "{@pagebreak}",
    ]
    fewer = ftbquests_snbt_extractor.merge_translated_lines(template, ["a"])
    assert fewer == ["a", "", "{image:ftbquests:x.png width:50}", "{@pagebreak}"]


def test_lang_patch_keeps_blank_image_and_pagebreak_lines(tmp_path: Path):
    src = tmp_path / "en_us.snbt"
    dst = tmp_path / "zh_tw.snbt"
    body = "{\n\tq.quest_desc: [" + " ".join(json.dumps(x) for x in _DESC) + "]\n}\n"
    src.write_text(body, encoding="utf-8")
    dst.write_text(body, encoding="utf-8")

    ftbquests_snbt_inject.patch_lang_snbt_file(
        str(src), str(dst), {"q.quest_desc": "譯一\n譯二"}
    )

    text = dst.read_text(encoding="utf-8")
    assert '"譯一"' in text and '"譯二"' in text
    assert '""' in text
    assert "{image:ftbquests:x.png width:50}" in text
    assert "{@pagebreak}" in text


def test_quest_patch_keeps_blank_and_marker_lines(tmp_path: Path):
    path = tmp_path / "chapter.snbt"
    path.write_text(
        '{\n\tquests: [{\n\t\tid: "AB"\n\t\tdescription: ['
        + " ".join(json.dumps(x) for x in _DESC)
        + "]\n\t}]\n}\n",
        encoding="utf-8",
    )
    ftbquests_snbt_inject.patch_quest_snbt_file(
        str(path), str(path), {"id:AB|description": "譯一\n譯二"}
    )
    text = path.read_text(encoding="utf-8")
    assert '"譯一"' in text and '"譯二"' in text
    assert '""' in text and "{@pagebreak}" in text and "{image:" in text


def test_subtitle_keys_are_extracted_from_lang_file(tmp_path: Path):
    root = snbt.loads(
        '{ quest.A.title: "T" quest.A.quest_subtitle: "Sub" chapter.B.chapter_subtitle: "CS" other: "x" }'
    )
    out = ftbquests_snbt_extractor.extract_lang_file("en_us.snbt", root)
    assert set(out) == {
        "en_us.snbt|quest.A.title",
        "en_us.snbt|quest.A.quest_subtitle",
        "en_us.snbt|chapter.B.chapter_subtitle",
    }


def test_inject_only_run_reports_duration_without_export_step(
    tmp_path: Path, monkeypatch
):
    quests = tmp_path / "config" / "ftbquests" / "quests"
    (quests / "lang").mkdir(parents=True)
    (quests / "lang" / "en_us.snbt").write_text(
        '{ q.title: "Hello" }', encoding="utf-8"
    )
    out = tmp_path / "Output"
    zh_dir = (
        out
        / "ftbquests"
        / "LM翻譯輸出"
        / "config"
        / "ftbquests"
        / "quests"
        / "lang"
        / "zh_tw"
    )
    zh_dir.mkdir(parents=True)
    (zh_dir / "ftb_lang.json").write_text(
        json.dumps({"en_us.snbt|q.title": "你好"}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(
        ftbquests_lmtranslator, "translate_ftb_pending_to_zh_tw", lambda **k: {}
    )

    result = ftb_translator.run_ftb_pipeline(
        directory_path=str(tmp_path),
        output_dir=str(out),
        step_export=False,
        step_clean=False,
        step_translate=False,
        step_inject=True,
    )

    assert result["inject"]["lang"]["patched_keys_changed"] == 1
