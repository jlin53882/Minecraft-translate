from __future__ import annotations

import json
from pathlib import Path

from translation_tool.core import kubejs_translator


class _FakeSession:
    def __init__(self) -> None:
        self.values: list[float] = []

    def set_progress(self, value: float) -> None:
        self.values.append(value)


def test_step2_translate_lm_requires_output_dir_or_translated_dir() -> None:
    try:
        kubejs_translator.step2_translate_lm(pending_dir="x")
    except ValueError as e:
        assert "output_dir 或 translated_dir" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_run_kubejs_pipeline_skips_step2_when_no_pending_keys(
    tmp_path: Path, monkeypatch
) -> None:
    session = _FakeSession()
    lang = tmp_path / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(json.dumps({"item": "Item"}), encoding="utf-8")
    (lang / "zh_tw.json").write_text(json.dumps({"item": "物品"}), encoding="utf-8")

    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(tmp_path),
        output_dir=str(tmp_path / "Output"),
        session=session,
        dry_run=False,
        step_extract=True,
        step_translate=True,
        step_inject=False,
    )

    assert result["step2"]["skipped"] is True
    assert result["step2"]["reason"] == "pending keys = 0"
    assert result["step3"]["skipped"] is True
    assert session.values and session.values[-1] >= 0.66


def test_run_kubejs_pipeline_extracts_translates_and_injects_literals(
    tmp_path: Path,
) -> None:
    """完整 fake-translation 流程須保留 literal 文字並寫回各種 JS 呼叫。"""
    kubejs_dir = tmp_path / "kubejs" / "client_scripts"
    kubejs_dir.mkdir(parents=True)
    source_path = kubejs_dir / "test.js"
    source = (
        "event.add('minecraft:dirt', Text.of('Event text'))\n"
        "scene.text('scene', 'Scene text')\n"
        "ItemEvents.tooltip(event => {\n"
        "  event.add('minecraft:stone', [Text.literal('Tooltip text')]);\n"
        "});"
    )
    source_path.write_text(source, encoding="utf-8")

    def fake_translator(*, pending_dir: str, output_dir: str, **kwargs) -> dict:
        """以固定 fake 翻譯取代 API，但保留真實 pipeline 的路徑契約。"""
        pending_root = Path(pending_dir)
        translated_root = Path(output_dir)
        total_keys = 0
        written_files = 0
        for pending_path in pending_root.rglob("*.json"):
            data = json.loads(pending_path.read_text(encoding="utf-8"))
            translated = {key: f"譯:{value}" for key, value in data.items()}
            destination = translated_root / pending_path.relative_to(pending_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(translated, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            total_keys += len(translated)
            written_files += 1
        return {"files": written_files, "total_keys": total_keys}

    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(tmp_path / "kubejs"),
        output_dir=str(tmp_path / "Output"),
        translator_fn=fake_translator,
        step_extract=True,
        step_translate=True,
        step_inject=True,
    )

    output_path = (
        tmp_path
        / "Output"
        / "kubejs"
        / "完成"
        / "kubejs"
        / "client_scripts"
        / "test.js"
    )
    output = output_path.read_text(encoding="utf-8")

    assert result["step1"]["extract"]["errors_count"] == 0
    assert result["step2"]["total_keys"] >= 3
    assert result["step3"]["patched_js_files"] == 1
    assert "譯:Event text" in output
    assert "譯:Scene text" in output
    assert "譯:Tooltip text" in output
    assert source_path.read_text(encoding="utf-8") == source


def _fake_translate_pending(*, pending_dir: str, output_dir: str, **kwargs) -> dict:
    """將每個待翻譯檔案複製成固定繁中結果，不呼叫外部 API。"""
    pending_root = Path(pending_dir)
    translated_root = Path(output_dir)
    files = 0
    total_keys = 0
    for pending_path in pending_root.rglob("*.json"):
        data = json.loads(pending_path.read_text(encoding="utf-8"))
        translated = {key: f"譯:{value}" for key, value in data.items()}
        relative = pending_path.relative_to(pending_root)
        if "lang" in relative.parts and relative.name == "en_us.json":
            relative = relative.with_name("zh_tw.json")
        destination = translated_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(translated, ensure_ascii=False), encoding="utf-8"
        )
        files += 1
        total_keys += len(translated)
    return {"files": files, "total_keys": total_keys}


def test_inject_merges_ai_lang_output_with_existing_effective_translation(
    tmp_path: Path,
) -> None:
    kubejs = tmp_path / "kubejs"
    lang = kubejs / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(
        json.dumps({"apple": "Apple", "book": "Book"}), encoding="utf-8"
    )
    (lang / "zh_tw.json").write_text(json.dumps({"apple": "蘋果"}), encoding="utf-8")

    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(kubejs),
        output_dir=str(tmp_path / "Output"),
        translator_fn=_fake_translate_pending,
    )

    final = (
        tmp_path
        / "Output"
        / "kubejs"
        / "完成"
        / "kubejs"
        / "assets"
        / "demo"
        / "lang"
        / "zh_tw.json"
    )
    assert json.loads(final.read_text(encoding="utf-8")) == {
        "apple": "蘋果",
        "book": "譯:Book",
    }


def test_step2_disabled_does_not_inject_previous_translation_output(
    tmp_path: Path,
) -> None:
    kubejs = tmp_path / "kubejs"
    script = kubejs / "client_scripts" / "tooltip.js"
    script.parent.mkdir(parents=True)
    original = "scene.text('scene', 'Fresh text')\n"
    script.write_text(original, encoding="utf-8")
    stale = (
        tmp_path
        / "Output"
        / "kubejs"
        / "LM翻譯後"
        / "kubejs"
        / "client_scripts"
        / "tooltip.json"
    )
    stale.parent.mkdir(parents=True)
    stale.write_text(
        json.dumps({"tooltip.js|scene.1": "舊的上輪翻譯"}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(kubejs),
        output_dir=str(tmp_path / "Output"),
        step_translate=False,
        step_inject=True,
    )

    output_script = (
        tmp_path
        / "Output"
        / "kubejs"
        / "完成"
        / "kubejs"
        / "client_scripts"
        / "tooltip.js"
    )
    assert result["step3"]["skipped"] is True
    assert (
        not output_script.exists()
        or output_script.read_text(encoding="utf-8") == original
    )


def test_fresh_run_does_not_include_raw_files_from_previous_source(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    seen: list[set[str]] = []

    def record_pending(*, pending_dir: str, output_dir: str, **kwargs) -> dict:
        keys = set()
        for path in Path(pending_dir).rglob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            keys.update(data)
            destination = Path(output_dir) / path.relative_to(pending_dir)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(data), encoding="utf-8")
        seen.append(keys)
        return {"files": len(seen), "total_keys": len(keys)}

    for name, text in (("old", "Old source"), ("new", "New source")):
        kubejs = tmp_path / name / "kubejs" / "client_scripts"
        kubejs.mkdir(parents=True)
        (kubejs / f"{name}.js").write_text(
            f"scene.text('scene', '{text}')\n", encoding="utf-8"
        )
        kubejs_translator.run_kubejs_pipeline(
            input_dir=str(kubejs.parent),
            output_dir=str(output),
            translator_fn=record_pending,
            step_inject=False,
        )

    assert len(seen) == 2
    assert len(seen[0]) == 1
    assert len(seen[1]) == 1
    assert any("new.js|scene." in key for key in seen[1])
    assert all("old.js|scene." not in key for key in seen[1])


def test_incremental_run_keeps_previous_source_and_imports_new_source(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    sources = [
        ("base", "Base text", "fresh"),
        ("patch", "Patch text", "incremental"),
    ]
    pending_keys: list[set[str]] = []

    def record_pending(*, pending_dir: str, output_dir: str, **kwargs) -> dict:
        keys = set()
        for path in Path(pending_dir).rglob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            keys.update(data)
            destination = Path(output_dir) / path.relative_to(pending_dir)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(data), encoding="utf-8")
        pending_keys.append(keys)
        return {"files": 1, "total_keys": len(keys)}

    for name, text, source_mode in sources:
        scripts = tmp_path / name / "kubejs" / "client_scripts"
        scripts.mkdir(parents=True)
        (scripts / f"{name}.js").write_text(
            f"scene.text('scene', '{text}')\n", encoding="utf-8"
        )
        kubejs_translator.run_kubejs_pipeline(
            input_dir=str(scripts.parent),
            output_dir=str(output),
            source_mode=source_mode,
            translator_fn=record_pending,
            step_inject=False,
        )

    assert len(pending_keys) == 2
    assert any("base.js|scene." in key for key in pending_keys[1])
    assert any("patch.js|scene." in key for key in pending_keys[1])


def test_incremental_replaces_changed_source_snapshot(tmp_path: Path) -> None:
    output = tmp_path / "Output"
    base_script = tmp_path / "base" / "kubejs" / "client_scripts" / "base.js"
    patch_script = tmp_path / "patch" / "kubejs" / "client_scripts" / "patch.js"
    base_script.parent.mkdir(parents=True)
    patch_script.parent.mkdir(parents=True)
    base_script.write_text("scene.text('scene', 'Base')\n", encoding="utf-8")
    patch_script.write_text("scene.text('scene', 'Old patch')\n", encoding="utf-8")

    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(base_script.parents[2]),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )
    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(patch_script.parents[2]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )

    patch_script.unlink()
    new_patch = patch_script.with_name("patch_v2.js")
    new_patch.write_text("scene.text('scene', 'New patch')\n", encoding="utf-8")
    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(patch_script.parents[2]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )

    pending = Path(result["paths"]["snapshot"]) / "待翻譯" / "kubejs"
    data = {}
    for path in pending.rglob("*.json"):
        data.update(json.loads(path.read_text(encoding="utf-8")))
    assert any("base.js|scene." in key for key in data)
    assert not any("patch.js|scene." in key for key in data)
    assert any("patch_v2.js|scene." in key for key in data)
    assert "New patch" in data.values()
    assert "Old patch" not in data.values()


def test_incremental_import_preserves_manual_effective_lang_translation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    base_lang = tmp_path / "base" / "kubejs" / "assets" / "demo" / "lang"
    base_lang.mkdir(parents=True)
    (base_lang / "en_us.json").write_text(
        json.dumps({"apple": "Apple"}), encoding="utf-8"
    )
    (base_lang / "zh_tw.json").write_text(
        json.dumps({"apple": "蘋果"}, ensure_ascii=False), encoding="utf-8"
    )
    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(base_lang.parents[3]),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )

    final_lang = (
        output
        / "kubejs"
        / "完成"
        / "kubejs"
        / "assets"
        / "demo"
        / "lang"
        / "zh_tw.json"
    )
    final_lang.write_text(
        json.dumps({"apple": "自訂譯文"}, ensure_ascii=False), encoding="utf-8"
    )
    patch_scripts = tmp_path / "patch" / "kubejs" / "client_scripts"
    patch_scripts.mkdir(parents=True)
    (patch_scripts / "patch.js").write_text(
        "scene.text('scene', 'Patch text')\n", encoding="utf-8"
    )

    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(patch_scripts.parent),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )

    snapshot_final = (
        Path(result["paths"]["snapshot"])
        / "完成"
        / "kubejs"
        / "assets"
        / "demo"
        / "lang"
        / "zh_tw.json"
    )
    pending_lang = (
        Path(result["paths"]["snapshot"])
        / "待翻譯"
        / "kubejs"
        / "assets"
        / "demo"
        / "lang"
        / "en_us.json"
    )
    assert json.loads(snapshot_final.read_text(encoding="utf-8")) == {
        "apple": "自訂譯文"
    }
    assert not pending_lang.exists()


def test_fresh_cleans_unchanged_stale_mirrors_and_preserves_user_edits(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    source_a = tmp_path / "a" / "kubejs" / "client_scripts"
    source_b = tmp_path / "b" / "kubejs" / "client_scripts"
    source_a.mkdir(parents=True)
    source_b.mkdir(parents=True)
    (source_a / "edited.js").write_text(
        "scene.text('scene', 'Edited text')\n", encoding="utf-8"
    )
    (source_a / "stale.js").write_text(
        "scene.text('scene', 'Stale text')\n", encoding="utf-8"
    )
    (source_b / "fresh.js").write_text(
        "scene.text('scene', 'Fresh text')\n", encoding="utf-8"
    )

    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(source_a.parent),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )
    canonical_pending = output / "kubejs" / "待翻譯" / "kubejs"
    edited_path = next(canonical_pending.rglob("edited.json"))
    stale_path = next(canonical_pending.rglob("stale.json"))
    edited_path.write_text('{"manual": "keep"}', encoding="utf-8")

    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(source_b.parent),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )

    current_pending = Path(result["paths"]["snapshot"]) / "待翻譯" / "kubejs"
    assert not list(current_pending.rglob("edited.json"))
    assert not list(current_pending.rglob("stale.json"))
    assert edited_path.read_text(encoding="utf-8") == '{"manual": "keep"}'
    assert not stale_path.exists()
    assert list(canonical_pending.rglob("fresh.json"))
