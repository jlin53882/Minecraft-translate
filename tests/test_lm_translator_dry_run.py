from __future__ import annotations

from pathlib import Path

import orjson

from translation_tool.core import lm_translator


def test_translate_directory_generator_dry_run_writes_preview_files(tmp_path: Path, monkeypatch) -> None:
    input_root = tmp_path / "input"
    out_root = tmp_path / "out"

    lang_file = input_root / "assets" / "demo_mod" / "lang" / "en_us.json"
    lang_file.parent.mkdir(parents=True, exist_ok=True)
    lang_file.write_bytes(orjson.dumps({"a": "hello"}))

    # Avoid touching real API key validation / cache persistence
    monkeypatch.setattr(lm_translator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(lm_translator, "reload_translation_cache", lambda: None)
    monkeypatch.setattr(lm_translator, "get_cache_dict_ref", lambda cache_type: {})

    # Make scan/extract deterministic across the new seam
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

    updates = list(
        lm_translator.translate_directory_generator(
            str(input_root),
            str(out_root),
            dry_run=True,
        )
    )

    assert updates, "generator should yield progress updates"

    preview_path = out_root / "_dry_run_preview.json"
    cache_hit_preview_path = out_root / "_dry_run_cache_hit_preview.json"

    assert preview_path.exists()
    assert cache_hit_preview_path.exists()

    preview = orjson.loads(preview_path.read_bytes())
    assert preview and preview[0]["path"] == "a"

    cache_preview = orjson.loads(cache_hit_preview_path.read_bytes())
    assert isinstance(cache_preview, list)
