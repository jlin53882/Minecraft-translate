from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from translation_tool.core import lm_translator
from translation_tool.translation_db import (
    KIND_ADVANCEMENT,
    DbSettings,
    ScanItem,
    TranslationDB,
)


def test_translate_directory_generator_cache_hit_writes_output_json(
    tmp_path: Path, monkeypatch
) -> None:
    input_root = tmp_path / "input"
    out_root = tmp_path / "out"

    lang_file = input_root / "assets" / "demo_mod" / "lang" / "en_us.json"
    lang_file.parent.mkdir(parents=True, exist_ok=True)
    lang_file.write_bytes(orjson.dumps({"a": "hello"}))

    monkeypatch.setattr(lm_translator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(lm_translator, "reload_translation_cache", lambda: None)

    # Cache hit should be accepted
    monkeypatch.setattr(lm_translator, "value_fully_translated", lambda value: True)

    def fake_get_cache_dict_ref(cache_type: str):
        if cache_type == "lang":
            return {"a": {"src": "hello", "dst": "你好"}}
        return {}

    monkeypatch.setattr(lm_translator, "get_cache_dict_ref", fake_get_cache_dict_ref)

    monkeypatch.setattr(
        lm_translator,
        "scan_translatable_files",
        lambda root: ([], [lang_file], [lang_file]),
    )
    monkeypatch.setattr(
        lm_translator,
        "extract_items_parallel",
        lambda **kw: [
            (
                {str(lang_file): {"a": "hello"}},
                [
                    {
                        "file": str(lang_file),
                        "path": "a",
                        "text": "hello",
                        "source_text": "hello",
                        "cache_type": "lang",
                    }
                ],
            )
        ],
    )

    list(
        lm_translator.translate_directory_generator(
            str(input_root),
            str(out_root),
            dry_run=False,
        )
    )

    outputs = list(out_root.rglob("*.json"))
    assert len(outputs) == 1

    payload = orjson.loads(outputs[0].read_bytes())
    assert payload["a"] == "你好"


@pytest.mark.parametrize("export_lang", [False, True])
def test_translate_directory_generator_advancement_cache_hit_round_trip(
    tmp_path: Path, monkeypatch, export_lang: bool
) -> None:
    input_root = tmp_path / "input"
    out_root = tmp_path / "out"
    advancement_file = (
        input_root / "data" / "demo_mod" / "advancements" / "lang" / "start.json"
    )
    advancement_file.parent.mkdir(parents=True, exist_ok=True)
    source = {
        "criteria": {"x": {"trigger": "minecraft:tick"}},
        "display": {
            "title": {
                "translate": "advancements.demo.start",
                "fallback": "Start Here",
                "extra": [{"text": " now", "bold": True}],
                "clickEvent": {"action": "run_command", "value": "/say hi"},
            }
        },
    }
    advancement_file.write_bytes(orjson.dumps(source))

    monkeypatch.setattr(lm_translator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(lm_translator, "reload_translation_cache", lambda: None)
    monkeypatch.setattr(lm_translator, "value_fully_translated", lambda value: True)
    monkeypatch.setattr(
        lm_translator,
        "get_cache_dict_ref",
        lambda cache_type: (
            {
                "display.title.fallback|Start Here": {
                    "src": "Start Here",
                    "dst": "從這裡開始",
                },
                "display.title.extra[0].text| now": {
                    "src": " now",
                    "dst": " 現在",
                },
            }
            if cache_type == "advancement"
            else {}
        ),
    )
    monkeypatch.setattr(
        lm_translator,
        "scan_translatable_files",
        lambda root: ([advancement_file], [], [advancement_file]),
    )
    monkeypatch.setattr(
        lm_translator,
        "extract_items_parallel",
        lambda **kw: [
            (
                {str(advancement_file): source},
                [
                    {
                        "file": str(advancement_file),
                        "path": "display.title.fallback",
                        "text": "Start Here",
                        "source_text": "Start Here",
                        "cache_type": "advancement",
                    },
                    {
                        "file": str(advancement_file),
                        "path": "display.title.extra[0].text",
                        "text": " now",
                        "source_text": " now",
                        "cache_type": "advancement",
                    },
                ],
            )
        ],
    )

    list(
        lm_translator.translate_directory_generator(
            str(input_root),
            str(out_root),
            dry_run=False,
            export_lang=export_lang,
        )
    )

    output_file = out_root / advancement_file.relative_to(input_root)
    assert output_file.exists()
    assert not list(out_root.rglob("*.lang"))
    payload = orjson.loads(output_file.read_bytes())
    assert payload["display"]["title"]["translate"] == "advancements.demo.start"
    assert payload["display"]["title"]["fallback"] == "從這裡開始"
    assert payload["display"]["title"]["extra"][0] == {"text": " 現在", "bold": True}
    assert (
        payload["display"]["title"]["clickEvent"]
        == source["display"]["title"]["clickEvent"]
    )


@pytest.mark.parametrize("export_lang", [False, True])
def test_translate_directory_generator_advancement_db_hit_round_trip(
    tmp_path: Path, monkeypatch, export_lang: bool
) -> None:
    """隔離資料庫命中後，仍由 Advancement writer 完成目標 JSON 注入。"""
    input_root = tmp_path / "input"
    out_root = tmp_path / "out"
    db_path = tmp_path / "mod-translation.db"
    advancement_file = (
        input_root / "data" / "demo_mod" / "advancements" / "lang" / "start.json"
    )
    advancement_file.parent.mkdir(parents=True, exist_ok=True)
    source = {
        "criteria": {"x": {"trigger": "minecraft:tick"}},
        "display": {
            "title": {
                "translate": "advancements.demo.start",
                "fallback": "Start Here",
                "extra": [{"text": " now", "italic": True}],
                "hoverEvent": {"action": "show_text", "contents": "Keep"},
            },
            "description": "Description text",
        },
    }
    advancement_file.write_bytes(orjson.dumps(source))

    db = TranslationDB(db_path)
    db.ingest(
        "1.20.1",
        [
            ScanItem(
                KIND_ADVANCEMENT,
                "demo_mod",
                "lang/start.json#display.title.fallback",
                "Start Here",
                "從這裡開始",
            ),
            ScanItem(
                KIND_ADVANCEMENT,
                "demo_mod",
                "lang/start.json#display.title.extra[0].text",
                " now",
                " 現在",
            ),
        ],
    )
    db.close()

    monkeypatch.setattr(lm_translator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(lm_translator, "reload_translation_cache", lambda: None)

    list(
        lm_translator.translate_directory_generator(
            str(input_root),
            str(out_root),
            dry_run=False,
            export_lang=export_lang,
            use_translation_db=True,
            translation_db_version="1.20.1",
            translation_db_settings_snapshot=DbSettings(
                enabled=True,
                path=str(db_path),
                version="1.20.1",
                write_back=False,
            ),
        )
    )

    output_file = out_root / advancement_file.relative_to(input_root)
    assert output_file.exists()
    assert not list(out_root.rglob("*.lang"))
    payload = orjson.loads(output_file.read_bytes())
    assert payload["display"]["title"]["fallback"] == "從這裡開始"
    assert payload["display"]["title"]["extra"][0] == {
        "text": " 現在",
        "italic": True,
    }
    assert (
        payload["display"]["title"]["translate"]
        == source["display"]["title"]["translate"]
    )
    assert (
        payload["display"]["title"]["hoverEvent"]
        == source["display"]["title"]["hoverEvent"]
    )
    assert payload["display"]["description"] == source["display"]["description"]


@pytest.mark.parametrize("export_lang", [False, True])
def test_translate_directory_generator_advancement_ai_result_preserves_json(
    tmp_path: Path, monkeypatch, export_lang: bool
) -> None:
    """AI 結果也不能因 Advancement 子路徑含 lang 而輸出成 .lang。"""
    input_root = tmp_path / "input"
    out_root = tmp_path / "out"
    advancement_file = (
        input_root / "data" / "demo_mod" / "advancements" / "lang" / "start.json"
    )
    advancement_file.parent.mkdir(parents=True, exist_ok=True)
    source = {
        "criteria": {"tick": {"trigger": "minecraft:tick"}},
        "display": {
            "title": {
                "translate": "advancements.demo.start",
                "text": "Start Here",
                "clickEvent": {"action": "run_command", "value": "/say hi"},
            }
        },
    }
    advancement_file.write_bytes(orjson.dumps(source))

    monkeypatch.setattr(lm_translator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(lm_translator, "reload_translation_cache", lambda: None)
    monkeypatch.setattr(lm_translator, "get_cache_dict_ref", lambda _type: {})
    monkeypatch.setattr(lm_translator, "add_to_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(lm_translator, "save_translation_cache", lambda: None)
    monkeypatch.setattr(
        lm_translator,
        "scan_translatable_files",
        lambda root: ([advancement_file], [], [advancement_file]),
    )
    monkeypatch.setattr(
        lm_translator,
        "extract_items_parallel",
        lambda **kw: [
            (
                {str(advancement_file): source},
                [
                    {
                        "file": str(advancement_file),
                        "path": "display.title.text",
                        "text": "Start Here",
                        "source_text": "Start Here",
                        "cache_type": "advancement",
                    }
                ],
            )
        ],
    )

    def fake_translate(batch, *, total):
        return [{**item, "text": "從這裡開始"} for item in batch], "AUTO"

    monkeypatch.setattr(lm_translator, "translate_batch_smart", fake_translate)

    list(
        lm_translator.translate_directory_generator(
            str(input_root),
            str(out_root),
            dry_run=False,
            export_lang=export_lang,
            use_translation_db=False,
        )
    )

    output_file = out_root / advancement_file.relative_to(input_root)
    assert output_file.exists()
    assert not list(out_root.rglob("*.lang"))
    payload = orjson.loads(output_file.read_bytes())
    assert payload["criteria"] == source["criteria"]
    assert payload["display"]["title"]["text"] == "從這裡開始"
    assert payload["display"]["title"]["translate"] == "advancements.demo.start"
    assert (
        payload["display"]["title"]["clickEvent"]
        == source["display"]["title"]["clickEvent"]
    )
