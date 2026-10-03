"""FTB Quests extraction-to-injection characterization coverage."""

from __future__ import annotations

import json
from pathlib import Path

from translation_tool.core import ftb_translator
from translation_tool.plugins.ftbquests import ftbquests_lmtranslator


def test_ftb_pipeline_runs_export_fake_translate_and_inject(
    tmp_path: Path, monkeypatch
) -> None:
    """真實串接 export/clean/fake-translate/inject，不只分別測 helper。"""
    quests_root = tmp_path / "config" / "ftbquests" / "quests"
    lang_root = quests_root / "lang"
    lang_root.mkdir(parents=True)
    (lang_root / "en_us.snbt").write_text(
        '{ quest.title: "English title", keep: "unchanged" }',
        encoding="utf-8",
    )
    (quests_root / "chapter.snbt").write_text(
        '{ id: "quest.one", title: "Quest title", description: ["Line one", "Line two"], keep: "unchanged" }',
        encoding="utf-8",
    )

    def fake_translate(*, input_lang_dir: str, output_lang_dir: str, **kwargs) -> dict:
        """用離線固定翻譯取代 LM，保留正式 translator 的輸出 layout。"""
        input_root = Path(input_lang_dir)
        output_root = Path(output_lang_dir)
        written_files = 0
        total_keys = 0
        for source in input_root.rglob("*.json"):
            data = json.loads(source.read_text(encoding="utf-8"))
            translated = {
                key: "\n".join(f"譯:{line}" for line in value.split("\n"))
                for key, value in data.items()
            }
            relative = source.relative_to(input_root)
            destination = output_root / "zh_tw" / relative.relative_to("en_us")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(translated, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            written_files += 1
            total_keys += len(translated)
        return {"written_files": written_files, "total_keys": total_keys}

    monkeypatch.setattr(
        ftbquests_lmtranslator,
        "translate_ftb_pending_to_zh_tw",
        fake_translate,
    )

    result = ftb_translator.run_ftb_pipeline(
        directory_path=str(tmp_path),
        output_dir=str(tmp_path / "Output"),
        step_export=True,
        step_clean=True,
        step_translate=True,
        step_inject=True,
        dry_run=False,
    )

    output_config = (
        tmp_path / "Output" / "ftbquests" / "完成" / "config" / "ftbquests" / "quests"
    )
    output_lang = output_config / "lang" / "zh_tw.snbt"
    output_quest = output_config / "chapter.snbt"
    output_lang_text = output_lang.read_text(encoding="utf-8")
    output_quest_text = output_quest.read_text(encoding="utf-8")

    assert result["raw_paths"]["written_langs"] == ["en_us"]
    assert result["lm_translate"]["total_keys"] >= 3
    assert result["inject"]["lang"]["patched_keys_changed"] == 1
    assert result["inject"]["quests"]["patched_keys_changed"] == 2
    assert 'quest.title: "譯:English title"' in output_lang_text
    assert 'title: "譯:Quest title"' in output_quest_text
    assert '"譯:Line one"' in output_quest_text
    assert '"譯:Line two"' in output_quest_text
    assert 'keep: "unchanged"' in output_quest_text
