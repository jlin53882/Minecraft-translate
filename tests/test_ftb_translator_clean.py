from __future__ import annotations

from pathlib import Path

import orjson

from translation_tool.core import ftb_translator


def test_clean_ftbquests_from_raw_outputs_pending_and_zh_tw(tmp_path: Path) -> None:
    raw_root = tmp_path / "Output" / "ftbquests" / "raw" / "config" / "ftbquests" / "quests" / "lang"
    (raw_root / "en_us").mkdir(parents=True)
    (raw_root / "zh_cn").mkdir(parents=True)

    (raw_root / "en_us" / "ftb_lang.json").write_bytes(orjson.dumps({"a": "A", "b": "B"}))
    (raw_root / "en_us" / "ftb_quests.json").write_bytes(orjson.dumps({"q1": "Q1", "q2": "Q2"}))
    (raw_root / "zh_cn" / "ftb_lang.json").write_bytes(orjson.dumps({"a": "簡中A"}))
    (raw_root / "zh_cn" / "ftb_quests.json").write_bytes(orjson.dumps({"q1": "簡中Q1"}))

    result = ftb_translator.clean_ftbquests_from_raw(str(tmp_path), output_dir=str(tmp_path / "Output"))

    pending_dir = Path(result["en_pending_dir"])
    zh_tw_dir = Path(result["zh_tw_dir"])

    assert result["has_twcn_source"] is True
    assert orjson.loads((pending_dir / "ftb_lang.json").read_bytes()) == {"b": "B"}
    assert orjson.loads((pending_dir / "ftb_quests.json").read_bytes()) == {"q2": "Q2"}
    assert orjson.loads((zh_tw_dir / "ftb_lang.json").read_bytes()) == {"a": "簡中A"}
    assert orjson.loads((zh_tw_dir / "ftb_quests.json").read_bytes()) == {"q1": "簡中Q1"}
