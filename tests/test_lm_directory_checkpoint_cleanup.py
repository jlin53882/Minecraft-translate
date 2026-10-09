"""目錄翻譯 orchestration 與 checkpoint 終態清理契約測試。"""

from __future__ import annotations

import inspect

import pytest

from translation_tool.core import lm_translator
from translation_tool.core.lm_translator_shared_loop import TranslateLoopResult


def _configure_directory_run(tmp_path, monkeypatch):
    input_root = tmp_path / "input"
    lang_file = input_root / "assets" / "demo" / "lang" / "en_us.json"
    lang_file.parent.mkdir(parents=True)
    lang_file.write_text('{"demo.key": "Hello"}', encoding="utf-8")
    item = {
        "file": str(lang_file),
        "path": "demo.key",
        "text": "Hello",
        "source_text": "Hello",
        "cache_type": "lang",
    }

    monkeypatch.setattr(lm_translator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(lm_translator, "reload_translation_cache", lambda: None)
    monkeypatch.setattr(lm_translator, "get_cache_dict_ref", lambda _cache_type: {})
    monkeypatch.setattr(
        lm_translator,
        "scan_translatable_files",
        lambda _root: ([], [lang_file], [lang_file]),
    )
    monkeypatch.setattr(
        lm_translator,
        "extract_items_parallel",
        lambda **_kwargs: [({str(lang_file): {"demo.key": "Hello"}}, [item])],
    )
    return input_root, [item]


@pytest.mark.parametrize(
    ("status", "processed", "should_retain"),
    [
        ("CANCELLED", 1, True),
        ("FAILED", 1, True),
        ("PARTIAL", 1, True),
        ("ALL_KEYS_EXHAUSTED", 1, True),
        ("DONE", 0, True),
        ("DONE", 1, False),
    ],
)
def test_only_full_done_run_clears_checkpoint(
    tmp_path, monkeypatch, status, processed, should_retain
):
    checkpoint = tmp_path / "logs" / "translation_checkpoint.json"
    monkeypatch.setattr(lm_translator, "CHECKPOINT_FILE", str(checkpoint))
    input_root, items = _configure_directory_run(tmp_path, monkeypatch)
    fingerprint = lm_translator.compute_checkpoint_fingerprint(str(input_root), items)
    lm_translator.save_checkpoint(
        1,
        0,
        1,
        items,
        str(tmp_path / "output"),
        input_dir=str(input_root),
        fingerprint=fingerprint,
    )

    monkeypatch.setattr(
        lm_translator,
        "run_translator_skeleton",
        lambda *_args, **_kwargs: TranslateLoopResult(
            status=status,
            processed=processed,
            total=1,
            completed_calls=1,
            elapsed_sec=0.0,
            exhausted=status == "ALL_KEYS_EXHAUSTED",
        ),
    )

    list(
        lm_translator.translate_directory_generator(
            str(input_root), str(tmp_path / "output")
        )
    )

    assert checkpoint.exists() is should_retain


def test_dry_run_retains_existing_checkpoint(tmp_path, monkeypatch):
    checkpoint = tmp_path / "logs" / "translation_checkpoint.json"
    monkeypatch.setattr(lm_translator, "CHECKPOINT_FILE", str(checkpoint))
    input_root, items = _configure_directory_run(tmp_path, monkeypatch)
    fingerprint = lm_translator.compute_checkpoint_fingerprint(str(input_root), items)
    lm_translator.save_checkpoint(
        1,
        0,
        1,
        items,
        str(tmp_path / "output"),
        input_dir=str(input_root),
        fingerprint=fingerprint,
    )

    list(
        lm_translator.translate_directory_generator(
            str(input_root), str(tmp_path / "output"), dry_run=True
        )
    )

    assert checkpoint.exists()


def test_directory_entrypoint_remains_orchestration_level():
    source = inspect.getsource(lm_translator.translate_directory_generator)
    work_source = inspect.getsource(lm_translator._translate_directory_work)

    assert len(source.splitlines()) <= 110
    for helper in (
        "_extract_directory_items",
        "_apply_cached_directory_items",
        "_write_directory_previews",
        "_run_directory_translation",
    ):
        assert helper in source + work_source
