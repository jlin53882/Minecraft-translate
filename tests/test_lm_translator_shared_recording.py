from __future__ import annotations

from pathlib import Path
import csv
import json

from translation_tool.core.lm_translator_shared import TranslationRecorder


def test_translation_recorder_exports_json_and_csv_with_extra_columns(tmp_path: Path) -> None:
    rec = TranslationRecorder()
    rec.record(
        cache_type="lang",
        file_id="demo.json",
        path="item.demo.name",
        src="Diamond",
        dst="鑽石",
        cache_hit=False,
        extra={"batch": 2},
    )

    json_path = rec.export_json(tmp_path / "translation_map.json")
    csv_path = rec.export_csv(tmp_path / "translation_map.csv")

    json_payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert json_payload[0]["cache_type"] == "lang"
    assert json_payload[0]["batch"] == 2

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert rows[0]["cache_type"] == "lang"
    assert rows[0]["file_id"] == "demo.json"
    assert rows[0]["batch"] == "2"
