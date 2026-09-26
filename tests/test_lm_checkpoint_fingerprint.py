"""翻譯 checkpoint 必須綁定來源資料，避免套用到其他資料夾而跳過項目。"""

from __future__ import annotations

import json
from pathlib import Path

import orjson

from translation_tool.core import lm_translator


def _setup(tmp_path: Path, monkeypatch, name: str, n: int):
    """建立 n 筆待翻譯項目，並把翻譯/快取等外部依賴換成假實作。"""
    input_root = tmp_path / name
    lang_file = input_root / "assets" / "demo" / "lang" / "en_us.json"
    lang_file.parent.mkdir(parents=True, exist_ok=True)
    data = {f"k{i}": f"{name} text {i}" for i in range(n)}
    lang_file.write_bytes(orjson.dumps(data))
    items = [
        {
            "file": str(lang_file),
            "path": k,
            "text": v,
            "source_text": v,
            "cache_type": "lang",
        }
        for k, v in data.items()
    ]

    monkeypatch.setattr(lm_translator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(lm_translator, "reload_translation_cache", lambda: None)
    monkeypatch.setattr(lm_translator, "get_cache_dict_ref", lambda cache_type: {})
    monkeypatch.setattr(lm_translator, "add_to_cache", lambda *a, **k: None)
    monkeypatch.setattr(lm_translator, "save_translation_cache", lambda *a, **k: None)
    monkeypatch.setattr(
        lm_translator,
        "scan_translatable_files",
        lambda root: ([], [lang_file], [lang_file]),
    )
    monkeypatch.setattr(
        lm_translator,
        "extract_items_parallel",
        lambda **kw: [({str(lang_file): dict(data)}, [dict(it) for it in items])],
    )

    def fake_translate(batch, total):
        return [dict(it, text="譯:" + it["text"]) for it in batch], "AUTO"

    monkeypatch.setattr(lm_translator, "translate_batch_smart", fake_translate)
    return input_root, items


def _run(input_root: Path, out_root: Path) -> dict:
    list(lm_translator.translate_directory_generator(str(input_root), str(out_root)))
    outputs = list(out_root.rglob("*.json"))
    outputs = [p for p in outputs if p.name != "translation_map.json"]
    assert len(outputs) == 1
    return orjson.loads(outputs[0].read_bytes())


def test_fingerprint_depends_on_source_and_content(tmp_path):
    items = [{"file": "a.json", "path": "k", "text": "hello"}]
    base = lm_translator.compute_checkpoint_fingerprint(str(tmp_path / "A"), items)

    assert base == lm_translator.compute_checkpoint_fingerprint(
        str(tmp_path / "A"), items
    )
    assert base != lm_translator.compute_checkpoint_fingerprint(
        str(tmp_path / "B"), items
    )
    changed = [{"file": "a.json", "path": "k", "text": "world"}]
    assert base != lm_translator.compute_checkpoint_fingerprint(
        str(tmp_path / "A"), changed
    )


def test_checkpoint_from_other_folder_with_same_count_is_ignored(tmp_path, monkeypatch):
    """舊版只比對筆數：B 會沿用 A 的 checkpoint 而跳過前 200 筆（保留英文）。"""
    checkpoint = tmp_path / "logs" / "checkpoint.json"
    monkeypatch.setattr(lm_translator, "CHECKPOINT_FILE", str(checkpoint))
    input_b, _ = _setup(tmp_path, monkeypatch, "B", 314)
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(
        json.dumps(
            {
                "batch_index": 2,
                "completed_count": 200,
                "total": 314,
                "output_dir": str(tmp_path / "A_out"),
            }
        ),
        encoding="utf-8",
    )

    result = _run(input_b, tmp_path / "B_out")

    assert len(result) == 314
    assert all(v.startswith("譯:") for v in result.values())
    assert not checkpoint.exists()


def test_checkpoint_of_same_data_still_resumes(tmp_path, monkeypatch):
    """同一批資料的 checkpoint 仍可續傳（只翻剩餘項目）。"""
    checkpoint = tmp_path / "logs" / "checkpoint.json"
    monkeypatch.setattr(lm_translator, "CHECKPOINT_FILE", str(checkpoint))
    input_a, items = _setup(tmp_path, monkeypatch, "A", 10)
    fingerprint = lm_translator.compute_checkpoint_fingerprint(str(input_a), items)
    lm_translator.save_checkpoint(
        2,
        4,
        10,
        items[4:],
        str(tmp_path / "A_out"),
        input_dir=str(input_a),
        fingerprint=fingerprint,
    )

    sent = []
    original = lm_translator.translate_batch_smart

    def spy(batch, total):
        sent.extend(it["path"] for it in batch)
        return original(batch, total)

    monkeypatch.setattr(lm_translator, "translate_batch_smart", spy)

    _run(input_a, tmp_path / "A_out")

    assert sent == [f"k{i}" for i in range(4, 10)]
