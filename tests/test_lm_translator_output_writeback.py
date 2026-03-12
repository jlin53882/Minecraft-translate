from __future__ import annotations

from pathlib import Path

import orjson

from translation_tool.core import lm_translator


def test_translate_directory_generator_cache_hit_writes_output_json(tmp_path: Path, monkeypatch) -> None:
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
        lambda **kwargs: (
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
        ),
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
