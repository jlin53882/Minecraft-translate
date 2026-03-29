from __future__ import annotations

from pathlib import Path
import json

from translation_tool.core.lm_translator_shared import (
    TouchSet,
    write_cache_hit_preview,
    write_dry_run_preview,
)


def test_write_dry_run_preview_writes_meta_count_and_items(tmp_path: Path) -> None:
    out = write_dry_run_preview(
        tmp_path,
        [{"path": "a", "text": "hello"}],
        meta={"mode": "dry-run"},
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["meta"] == {"mode": "dry-run"}
    assert payload["count"] == 1
    assert payload["items"][0]["path"] == "a"


def test_write_cache_hit_preview_keeps_only_expected_fields(tmp_path: Path) -> None:
    out = write_cache_hit_preview(
        tmp_path,
        [
            {
                "file": "demo.json",
                "path": "entry.text",
                "source_text": "Hello",
                "text": "哈囉",
                "cache_type": "patchouli",
                "ignored": "x",
            }
        ],
        meta={"kind": "cache-hit"},
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["meta"] == {"kind": "cache-hit"}
    assert payload["count"] == 1
    assert payload["items"] == [
        {
            "file": "demo.json",
            "path": "entry.text",
            "source_text": "Hello",
            "text": "哈囉",
            "cache_type": "patchouli",
        }
    ]


def test_touch_set_flushes_each_touched_file_once() -> None:
    touched: list[str] = []
    touch_set = TouchSet()
    touch_set.touch("a.json")
    touch_set.touch("a.json")
    touch_set.touch("b.json")

    touch_set.flush(touched.append)

    assert sorted(touched) == ["a.json", "b.json"]
    assert touch_set.touched == set()
