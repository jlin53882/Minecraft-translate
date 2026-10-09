from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.core import kubejs_translator, kubejs_translator_state


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


def _private_snapshot_layer(
    result: dict, output: Path, manifest_key: str, default: str
) -> Path:
    manifest = json.loads(
        (output / "kubejs" / ".pipeline" / "current.json").read_text(encoding="utf-8")
    )
    return Path(result["paths"]["snapshot"]) / manifest.get(manifest_key, default)


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


def test_injected_final_commit_failure_keeps_current_final_and_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    kubejs = tmp_path / "kubejs"
    script = kubejs / "client_scripts" / "tooltip.js"
    script.parent.mkdir(parents=True)
    script.write_text("scene.text('scene', 'Text')\n", encoding="utf-8")
    output = tmp_path / "Output"
    first = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(kubejs),
        output_dir=str(output),
        translator_fn=_fake_translate_pending,
    )
    snapshot = Path(first["paths"]["snapshot"])
    manifest_path = output / "kubejs" / ".pipeline" / "current.json"
    old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_final_relative = old_manifest.get("final_snapshot", "完成/kubejs")
    final_root = snapshot / old_final_relative
    old_final_path = next(final_root.rglob("tooltip.js"))
    old_final = old_final_path.read_text(encoding="utf-8")
    public_final_path = next(Path(first["paths"]["final"]).rglob("tooltip.js"))
    old_public_final = public_final_path.read_text(encoding="utf-8")
    real_replace = kubejs_translator_state.os.replace

    def fail_final_replace(source, destination):
        if "final-versions" in Path(destination).parts:
            raise OSError("simulated locked final directory")
        return real_replace(source, destination)

    monkeypatch.setattr(kubejs_translator_state.os, "replace", fail_final_replace)
    try:
        kubejs_translator.run_kubejs_pipeline(
            input_dir=str(kubejs),
            output_dir=str(output),
            step_extract=False,
            translator_fn=_fake_translate_pending,
        )
    except OSError as exc:
        assert "locked final directory" in str(exc)
    else:
        raise AssertionError("expected final directory commit failure")

    assert old_final_path.read_text(encoding="utf-8") == old_final
    assert public_final_path.read_text(encoding="utf-8") == old_public_final
    current_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert current_manifest["run_id"] == old_manifest["run_id"]
    assert current_manifest["final_snapshot"] == old_manifest["final_snapshot"]
    assert current_manifest["stage"] == "translated"


def test_translation_exception_does_not_destroy_previous_translated_snapshot(
    tmp_path: Path,
) -> None:
    kubejs = tmp_path / "kubejs"
    script = kubejs / "client_scripts" / "tooltip.js"
    script.parent.mkdir(parents=True)
    script.write_text("scene.text('scene', 'Text')\n", encoding="utf-8")
    first = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(kubejs),
        output_dir=str(tmp_path / "Output"),
        translator_fn=_fake_translate_pending,
        step_inject=False,
    )
    state_manifest = json.loads(
        (tmp_path / "Output" / "kubejs" / ".pipeline" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    translated_root = Path(first["paths"]["snapshot"]) / state_manifest.get(
        "translated_snapshot", "LM翻譯後/kubejs"
    )
    translated_before = {
        path.relative_to(translated_root).as_posix(): path.read_bytes()
        for path in translated_root.rglob("*.json")
    }
    old_manifest = json.loads(
        (tmp_path / "Output" / "kubejs" / ".pipeline" / "current.json").read_text(
            encoding="utf-8"
        )
    )

    def fail_after_partial_write(*, output_dir: str, **kwargs) -> dict:
        partial = Path(output_dir) / "partial.json"
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text('{"partial":"不完整"}', encoding="utf-8")
        raise RuntimeError("simulated translator failure")

    try:
        kubejs_translator.run_kubejs_pipeline(
            input_dir=str(kubejs),
            output_dir=str(tmp_path / "Output"),
            step_extract=False,
            step_inject=False,
            translator_fn=fail_after_partial_write,
        )
    except RuntimeError as exc:
        assert "translator failure" in str(exc)
    else:
        raise AssertionError("expected translator failure")

    translated_after = {
        path.relative_to(translated_root).as_posix(): path.read_bytes()
        for path in translated_root.rglob("*.json")
    }
    current_manifest = json.loads(
        (tmp_path / "Output" / "kubejs" / ".pipeline" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    assert translated_after == translated_before
    assert (
        current_manifest["translated_snapshot"] == old_manifest["translated_snapshot"]
    )


def test_public_mirror_conflicts_are_returned_with_snapshot_guidance(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    old_scripts = tmp_path / "old" / "kubejs" / "client_scripts"
    old_scripts.mkdir(parents=True)
    (old_scripts / "old.js").write_text(
        "scene.text('scene', 'Old text')\n", encoding="utf-8"
    )
    first = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(old_scripts.parent),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )
    public_pending = next(Path(first["paths"]["pending"]).rglob("old.json"))
    public_pending.write_text('{"manual":"keep"}', encoding="utf-8")

    new_scripts = tmp_path / "new" / "kubejs" / "client_scripts"
    new_scripts.mkdir(parents=True)
    (new_scripts / "new.js").write_text(
        "scene.text('scene', 'New text')\n", encoding="utf-8"
    )
    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(new_scripts.parent),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )

    assert public_pending.read_text(encoding="utf-8") == '{"manual":"keep"}'
    conflicts = result["mirror_conflicts"]
    assert any(
        conflict["path"].endswith("client_scripts/old.json")
        and conflict["category"] == "pending"
        and conflict["snapshot_path"]
        for conflict in conflicts
    )


def test_incremental_english_change_does_not_reuse_previous_ai_translation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    lang = tmp_path / "pack" / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    english = lang / "en_us.json"
    english.write_text(json.dumps({"apple": "Apple"}), encoding="utf-8")

    first = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        translator_fn=_fake_translate_pending,
        step_inject=True,
    )
    first_snapshot = Path(first["paths"]["snapshot"])
    first_final = first_snapshot / json.loads(
        (output / "kubejs" / ".pipeline" / "current.json").read_text(encoding="utf-8")
    ).get("final_snapshot", "完成/kubejs")
    assert (
        json.loads(
            (first_final / "assets" / "demo" / "lang" / "zh_tw.json").read_text(
                encoding="utf-8"
            )
        )["apple"]
        == "譯:Apple"
    )

    english.write_text(json.dumps({"apple": "Banana"}), encoding="utf-8")
    second = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )
    snapshot = Path(second["paths"]["snapshot"])
    pending = json.loads(
        (
            snapshot / "待翻譯" / "kubejs" / "assets" / "demo" / "lang" / "en_us.json"
        ).read_text(encoding="utf-8")
    )
    final_rel = json.loads(
        (output / "kubejs" / ".pipeline" / "current.json").read_text(encoding="utf-8")
    ).get("final_snapshot", "完成/kubejs")
    final = snapshot / final_rel / "assets" / "demo" / "lang" / "zh_tw.json"
    assert pending == {"apple": "Banana"}
    assert (
        not final.exists()
        or "譯:Apple" not in json.loads(final.read_text(encoding="utf-8")).values()
    )


def test_same_source_incremental_run_keeps_verified_manual_public_translation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    lang = tmp_path / "pack" / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(json.dumps({"apple": "Apple"}), encoding="utf-8")
    (lang / "zh_tw.json").write_text(
        json.dumps({"apple": "蘋果"}, ensure_ascii=False), encoding="utf-8"
    )
    first = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )
    public_final = (
        Path(first["paths"]["final"]) / "assets" / "demo" / "lang" / "zh_tw.json"
    )
    public_final.write_text(
        json.dumps({"apple": "人工校訂"}, ensure_ascii=False), encoding="utf-8"
    )

    second = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )
    snapshot = Path(second["paths"]["snapshot"])
    current_final = (
        snapshot / "完成" / "kubejs" / "assets" / "demo" / "lang" / "zh_tw.json"
    )
    assert json.loads(current_final.read_text(encoding="utf-8")) == {
        "apple": "人工校訂"
    }


def test_incremental_zh_tw_source_change_wins_over_previous_final(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    lang = tmp_path / "pack" / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(json.dumps({"apple": "Apple"}), encoding="utf-8")
    traditional = lang / "zh_tw.json"
    traditional.write_text(
        json.dumps({"apple": "蘋果"}, ensure_ascii=False), encoding="utf-8"
    )
    (lang / "zh_cn.json").write_text(
        json.dumps({"apple": "苹果"}, ensure_ascii=False), encoding="utf-8"
    )
    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )

    traditional.write_text(
        json.dumps({"apple": "香蕉"}, ensure_ascii=False), encoding="utf-8"
    )
    second = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )
    snapshot = Path(second["paths"]["snapshot"])
    final = snapshot / "完成" / "kubejs" / "assets" / "demo" / "lang" / "zh_tw.json"
    assert json.loads(final.read_text(encoding="utf-8")) == {"apple": "香蕉"}


@pytest.mark.parametrize(
    ("language", "source_a_value", "source_b_value"),
    [
        ("en_us", "Apple", "Banana"),
        ("zh_cn", "苹果", "香蕉"),
        ("zh_tw", "蘋果", "香蕉"),
    ],
)
def test_incremental_lang_conflict_reports_sources_path_key_and_decision(
    tmp_path: Path, language: str, source_a_value: str, source_b_value: str
) -> None:
    output = tmp_path / "Output"
    roots = [tmp_path / "base", tmp_path / "patch"]
    for root, conflict_value in zip(roots, (source_a_value, source_b_value)):
        lang = root / "kubejs" / "assets" / "demo" / "lang"
        lang.mkdir(parents=True)
        values = {
            "en_us": "Apple",
            "zh_cn": "苹果",
            "zh_tw": "蘋果",
        }
        values[language] = conflict_value
        for lang_code, value in values.items():
            (lang / f"{lang_code}.json").write_text(
                json.dumps({"apple": value}, ensure_ascii=False), encoding="utf-8"
            )

    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(roots[0] / "kubejs"),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )
    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(roots[1] / "kubejs"),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )

    conflicts = result["step1"]["source_conflict_details"]
    conflict = next(
        item
        for item in conflicts
        if item["key"] == "apple"
        and item["relative_path"].endswith(f"/{language}.json")
    )
    assert conflict["source_a"]["root"] == str(roots[0] / "kubejs")
    assert conflict["source_b"]["root"] == str(roots[1] / "kubejs")
    assert conflict["relative_path"] == f"assets/demo/lang/{language}.json"
    assert "較早匯入" in conflict["decision"]
    assert conflict["source_a"]["value_hash"] != conflict["source_b"]["value_hash"]


def test_incremental_same_relative_script_keeps_source_identity_isolated(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    sources = [tmp_path / "base" / "kubejs", tmp_path / "patch" / "kubejs"]
    for root, text in zip(sources, ("Base tooltip", "Patch tooltip")):
        scripts = root / "client_scripts"
        scripts.mkdir(parents=True)
        (scripts / "shared.js").write_text(
            f"scene.text('scene', '{text}')\n", encoding="utf-8"
        )

    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(sources[0]),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )
    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(sources[1]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )

    pending = Path(result["paths"]["snapshot"]) / "待翻譯" / "kubejs"
    files = sorted(pending.rglob("shared.json"))
    assert len(files) == 2
    keys = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    assert keys[0] != keys[1]
    values = [next(iter(item.values())) for item in keys]
    assert set(values) == {"Base tooltip", "Patch tooltip"}


def test_partial_translation_output_is_not_committed_or_injected(
    tmp_path: Path,
) -> None:
    kubejs = tmp_path / "kubejs" / "client_scripts"
    kubejs.mkdir(parents=True)
    script = kubejs / "tooltip.js"
    original = "scene.text('scene', 'First')\nscene.text('scene', 'Second')\n"
    script.write_text(original, encoding="utf-8")

    def partial_translator(*, pending_dir: str, output_dir: str, **kwargs) -> dict:
        pending_path = next(Path(pending_dir).rglob("*.json"))
        data = json.loads(pending_path.read_text(encoding="utf-8"))
        destination = Path(output_dir) / pending_path.relative_to(pending_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps({next(iter(data)): "只有一筆"}, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"total_keys": 1}

    try:
        kubejs_translator.run_kubejs_pipeline(
            input_dir=str(kubejs.parent),
            output_dir=str(tmp_path / "Output"),
            translator_fn=partial_translator,
        )
    except RuntimeError as exc:
        assert "輸出不完整" in str(exc)
    else:
        raise AssertionError("expected incomplete translation output to be rejected")

    manifest = json.loads(
        (tmp_path / "Output" / "kubejs" / ".pipeline" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    snapshot = (
        tmp_path / "Output" / "kubejs" / ".pipeline" / "runs" / manifest["run_id"]
    )
    final_root = snapshot / "完成" / "kubejs"
    assert final_root.is_dir()
    assert not list(final_root.rglob("tooltip.js"))
    assert manifest["stage"] == "cleaned"
    assert "translated_snapshot" not in manifest


def test_cancel_after_translation_keeps_previous_translation_pointer(
    tmp_path: Path, monkeypatch
) -> None:
    kubejs = tmp_path / "kubejs" / "client_scripts"
    kubejs.mkdir(parents=True)
    (kubejs / "tooltip.js").write_text(
        "scene.text('scene', 'Text')\n", encoding="utf-8"
    )
    output = tmp_path / "Output"
    first = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(kubejs.parent),
        output_dir=str(output),
        translator_fn=_fake_translate_pending,
        step_inject=False,
    )
    before = json.loads(
        (output / "kubejs" / ".pipeline" / "current.json").read_text(encoding="utf-8")
    )

    monkeypatch.setattr(kubejs_translator, "is_cancelled", lambda: True)
    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(kubejs.parent),
        output_dir=str(output),
        step_extract=False,
        translator_fn=_fake_translate_pending,
        step_inject=True,
    )
    after = json.loads(
        (output / "kubejs" / ".pipeline" / "current.json").read_text(encoding="utf-8")
    )

    assert after["translated_snapshot"] == before["translated_snapshot"]
    assert result["step2"]["reason"] == "cancelled"
    assert result["step3"]["skipped"] is True
    assert not list(Path(first["paths"]["snapshot"]).glob(".translated-stage-*"))


def test_final_mirror_conflict_is_reported_and_public_edit_is_kept(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    old_scripts = tmp_path / "old" / "kubejs" / "client_scripts"
    old_scripts.mkdir(parents=True)
    (old_scripts / "old.js").write_text(
        "scene.text('scene', 'Old text')\n", encoding="utf-8"
    )
    first = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(old_scripts.parent),
        output_dir=str(output),
        translator_fn=_fake_translate_pending,
    )
    public_script = next(Path(first["paths"]["final"]).rglob("old.js"))
    public_script.write_text("// manual final\n", encoding="utf-8")

    new_scripts = tmp_path / "new" / "kubejs" / "client_scripts"
    new_scripts.mkdir(parents=True)
    (new_scripts / "new.js").write_text(
        "scene.text('scene', 'New text')\n", encoding="utf-8"
    )
    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(new_scripts.parent),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )

    assert public_script.read_text(encoding="utf-8") == "// manual final\n"
    assert any(
        conflict["category"] == "final"
        and conflict["path"].endswith("old.js")
        and conflict["snapshot_exists"] == "false"
        for conflict in result["mirror_conflicts"]
    )


def test_incremental_zh_cn_change_does_not_override_current_zh_tw(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    lang = tmp_path / "pack" / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(json.dumps({"apple": "Apple"}), encoding="utf-8")
    (lang / "zh_tw.json").write_text(
        json.dumps({"apple": "蘋果"}, ensure_ascii=False), encoding="utf-8"
    )
    simplified = lang / "zh_cn.json"
    simplified.write_text(
        json.dumps({"apple": "苹果"}, ensure_ascii=False), encoding="utf-8"
    )
    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )

    simplified.write_text(
        json.dumps({"apple": "香蕉"}, ensure_ascii=False), encoding="utf-8"
    )
    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )

    final_lang = (
        _private_snapshot_layer(result, output, "final_snapshot", "完成/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "zh_tw.json"
    )
    pending_lang = (
        _private_snapshot_layer(result, output, "pending_snapshot", "待翻譯/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "en_us.json"
    )
    assert json.loads(final_lang.read_text(encoding="utf-8")) == {"apple": "蘋果"}
    pending = (
        json.loads(pending_lang.read_text(encoding="utf-8"))
        if pending_lang.is_file()
        else {}
    )
    assert "apple" not in pending


def test_incremental_new_zh_cn_key_does_not_override_current_zh_tw(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    lang = tmp_path / "pack" / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(json.dumps({"apple": "Apple"}), encoding="utf-8")
    (lang / "zh_tw.json").write_text(
        json.dumps({"apple": "蘋果"}, ensure_ascii=False), encoding="utf-8"
    )
    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )

    (lang / "zh_cn.json").write_text(
        json.dumps({"apple": "香蕉"}, ensure_ascii=False), encoding="utf-8"
    )
    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )

    final_lang = (
        _private_snapshot_layer(result, output, "final_snapshot", "完成/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "zh_tw.json"
    )
    pending_lang = (
        _private_snapshot_layer(result, output, "pending_snapshot", "待翻譯/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "en_us.json"
    )
    assert json.loads(final_lang.read_text(encoding="utf-8")) == {"apple": "蘋果"}
    pending = (
        json.loads(pending_lang.read_text(encoding="utf-8"))
        if pending_lang.is_file()
        else {}
    )
    assert "apple" not in pending


def test_english_change_keeps_current_zh_tw_and_removes_key_from_pending(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    lang = tmp_path / "pack" / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    english = lang / "en_us.json"
    english.write_text(json.dumps({"apple": "Apple"}), encoding="utf-8")
    (lang / "zh_tw.json").write_text(
        json.dumps({"apple": "蘋果"}, ensure_ascii=False), encoding="utf-8"
    )
    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )

    english.write_text(json.dumps({"apple": "Banana"}), encoding="utf-8")
    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )

    final_lang = (
        _private_snapshot_layer(result, output, "final_snapshot", "完成/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "zh_tw.json"
    )
    pending_lang = (
        _private_snapshot_layer(result, output, "pending_snapshot", "待翻譯/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "en_us.json"
    )
    final_value = (
        json.loads(final_lang.read_text(encoding="utf-8"))
        if final_lang.is_file()
        else {}
    )
    assert final_value == {"apple": "蘋果"}
    pending = (
        json.loads(pending_lang.read_text(encoding="utf-8"))
        if pending_lang.is_file()
        else {}
    )
    assert "apple" not in pending


def test_incremental_cn_conflict_keeps_earlier_cn_record_and_current_tw(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    roots = [tmp_path / name / "kubejs" for name in ("base", "patch", "late")]
    for root in roots:
        (root / "assets" / "demo" / "lang").mkdir(parents=True)
    lang_a = roots[0] / "assets" / "demo" / "lang"
    (lang_a / "en_us.json").write_text(json.dumps({"apple": "Apple"}), encoding="utf-8")
    (lang_a / "zh_tw.json").write_text(
        json.dumps({"apple": "蘋果"}, ensure_ascii=False), encoding="utf-8"
    )
    lang_b = roots[1] / "assets" / "demo" / "lang"
    (lang_b / "zh_cn.json").write_text(
        json.dumps({"apple": "香蕉"}, ensure_ascii=False), encoding="utf-8"
    )
    lang_c = roots[2] / "assets" / "demo" / "lang"
    (lang_c / "zh_cn.json").write_text(
        json.dumps({"apple": "葡萄"}, ensure_ascii=False), encoding="utf-8"
    )

    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(roots[0]),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )
    kubejs_translator.run_kubejs_pipeline(
        input_dir=str(roots[1]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )
    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(roots[2]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )

    final_lang = (
        _private_snapshot_layer(result, output, "final_snapshot", "完成/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "zh_tw.json"
    )
    assert json.loads(final_lang.read_text(encoding="utf-8")) == {"apple": "蘋果"}
    cn_conflict = next(
        item
        for item in result["step1"]["source_conflict_details"]
        if item["relative_path"].endswith("zh_cn.json") and item["key"] == "apple"
    )
    assert cn_conflict["source_a"]["root"] == str(roots[1])
    assert cn_conflict["source_b"]["root"] == str(roots[2])
    assert "較早匯入" in cn_conflict["decision"]


@pytest.mark.parametrize("tw_value", ["", "{demo.apple}"])
def test_invalid_zh_tw_uses_zh_cn_and_keeps_placeholders(
    tmp_path: Path, tw_value: str
) -> None:
    output = tmp_path / "Output"
    lang = tmp_path / "pack" / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(json.dumps({"apple": "Apple"}), encoding="utf-8")
    (lang / "zh_tw.json").write_text(
        json.dumps({"apple": tw_value}, ensure_ascii=False), encoding="utf-8"
    )
    (lang / "zh_cn.json").write_text(
        json.dumps({"apple": "苹果 %s $(item.apple) §a${value}"}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )

    final_lang = (
        _private_snapshot_layer(result, output, "final_snapshot", "完成/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "zh_tw.json"
    )
    pending_lang = (
        _private_snapshot_layer(result, output, "pending_snapshot", "待翻譯/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "en_us.json"
    )
    value = json.loads(final_lang.read_text(encoding="utf-8"))["apple"]
    assert value.startswith("蘋果")
    assert all(token in value for token in ("%s", "$(item.apple)", "§a", "${value}"))
    pending = (
        json.loads(pending_lang.read_text(encoding="utf-8"))
        if pending_lang.is_file()
        else {}
    )
    assert "apple" not in pending


def test_changed_source_does_not_reuse_manual_public_translation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    lang = tmp_path / "pack" / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    english = lang / "en_us.json"
    english.write_text(json.dumps({"apple": "Apple"}), encoding="utf-8")
    first = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        translator_fn=_fake_translate_pending,
        step_inject=True,
    )
    public_final = (
        Path(first["paths"]["final"]) / "assets" / "demo" / "lang" / "zh_tw.json"
    )
    public_final.write_text(
        json.dumps({"apple": "人工校訂"}, ensure_ascii=False), encoding="utf-8"
    )
    english.write_text(json.dumps({"apple": "Banana"}), encoding="utf-8")

    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )

    pending_lang = (
        _private_snapshot_layer(result, output, "pending_snapshot", "待翻譯/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "en_us.json"
    )
    assert json.loads(pending_lang.read_text(encoding="utf-8")) == {"apple": "Banana"}
    final_lang = (
        _private_snapshot_layer(result, output, "final_snapshot", "完成/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "zh_tw.json"
    )
    final_values = (
        json.loads(final_lang.read_text(encoding="utf-8"))
        if final_lang.is_file()
        else {}
    )
    assert final_values.get("apple") != "人工校訂"
    assert json.loads(public_final.read_text(encoding="utf-8")) == {"apple": "人工校訂"}


def test_incremental_cn_merge_is_idempotent(tmp_path: Path) -> None:
    output = tmp_path / "Output"
    lang = tmp_path / "pack" / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(json.dumps({"apple": "Apple"}), encoding="utf-8")
    (lang / "zh_tw.json").write_text(
        json.dumps({"apple": "蘋果"}, ensure_ascii=False), encoding="utf-8"
    )
    (lang / "zh_cn.json").write_text(
        json.dumps({"apple": "苹果"}, ensure_ascii=False), encoding="utf-8"
    )

    results = [
        kubejs_translator.run_kubejs_pipeline(
            input_dir=str(lang.parents[3]),
            output_dir=str(output),
            source_mode=mode,
            step_translate=False,
            step_inject=False,
        )
        for mode in ("fresh", "incremental", "incremental")
    ]
    final_files = [
        _private_snapshot_layer(result, output, "final_snapshot", "完成/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "zh_tw.json"
        for result in results
    ]
    assert [json.loads(path.read_text(encoding="utf-8")) for path in final_files] == [
        {"apple": "蘋果"}
    ] * 3
    assert all(result["mirror_conflict_count"] == 0 for result in results)


def test_source_changed_after_step1_is_not_used_for_injection(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    base_script = tmp_path / "base" / "kubejs" / "client_scripts" / "base.js"
    base_script.parent.mkdir(parents=True)
    base_script.write_text("scene.text('scene', 'Base text')\n", encoding="utf-8")
    first = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(base_script.parents[2]),
        output_dir=str(output),
        translator_fn=_fake_translate_pending,
    )
    previous_manifest = json.loads(
        (output / "kubejs" / ".pipeline" / "current.json").read_text(encoding="utf-8")
    )
    previous_final = (
        Path(first["paths"]["snapshot"])
        / previous_manifest["final_snapshot"]
        / "client_scripts"
        / "base.js"
    )
    previous_final_bytes = previous_final.read_bytes()

    patch_script = tmp_path / "patch" / "kubejs" / "client_scripts" / "patch.js"
    patch_script.parent.mkdir(parents=True)
    patch_script.write_text("scene.text('scene', 'Patch text')\n", encoding="utf-8")

    def mutate_source_during_translation(
        *, pending_dir: str, output_dir: str, **kwargs
    ) -> dict:
        translated = _fake_translate_pending(
            pending_dir=pending_dir, output_dir=output_dir, **kwargs
        )
        patch_script.write_text(
            "scene.text('scene', 'Changed after Step 1')\n", encoding="utf-8"
        )
        return translated

    with pytest.raises(RuntimeError, match="來源.*(變更|重新抽取)"):
        kubejs_translator.run_kubejs_pipeline(
            input_dir=str(patch_script.parents[2]),
            output_dir=str(output),
            source_mode="incremental",
            translator_fn=mutate_source_during_translation,
        )

    current_manifest = json.loads(
        (output / "kubejs" / ".pipeline" / "current.json").read_text(encoding="utf-8")
    )
    assert current_manifest["stage"] == "translated"
    assert previous_final.read_bytes() == previous_final_bytes
    current_final = (
        output
        / "kubejs"
        / ".pipeline"
        / "runs"
        / current_manifest["run_id"]
        / current_manifest.get("final_snapshot", "完成/kubejs")
        / "client_scripts"
        / "patch.js"
    )
    assert not current_final.exists()


def test_source_changed_during_injection_uses_verified_snapshot_and_aborts_commit(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "Output"
    base_script = tmp_path / "base" / "kubejs" / "client_scripts" / "base.js"
    base_script.parent.mkdir(parents=True)
    base_script.write_text("scene.text('scene', 'Base text')\n", encoding="utf-8")
    first = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(base_script.parents[2]),
        output_dir=str(output),
        translator_fn=_fake_translate_pending,
    )
    first_manifest = json.loads(
        (output / "kubejs" / ".pipeline" / "current.json").read_text(encoding="utf-8")
    )
    old_final = (
        Path(first["paths"]["snapshot"])
        / first_manifest["final_snapshot"]
        / "client_scripts"
        / "base.js"
    )
    old_final_bytes = old_final.read_bytes()

    patch_script = tmp_path / "patch" / "kubejs" / "client_scripts" / "patch.js"
    patch_script.parent.mkdir(parents=True)
    patch_script.write_text("scene.text('scene', 'Patch text')\n", encoding="utf-8")
    real_step3_inject = kubejs_translator.step3_inject

    def mutate_live_source_after_injection(**kwargs) -> dict:
        patch_snapshot = next(
            Path(root) / "client_scripts" / "patch.js"
            for root in kwargs["source_roots"].values()
            if (Path(root) / "client_scripts" / "patch.js").is_file()
        )
        assert patch_snapshot.read_text(encoding="utf-8") == (
            "scene.text('scene', 'Patch text')\n"
        )
        injected = real_step3_inject(**kwargs)
        candidate = Path(kwargs["final_dir"]) / "client_scripts" / "patch.js"
        assert "譯:Patch text" in candidate.read_text(encoding="utf-8")
        patch_script.write_text(
            "scene.text('scene', 'Changed during injection')\n", encoding="utf-8"
        )
        return injected

    monkeypatch.setattr(
        kubejs_translator, "step3_inject", mutate_live_source_after_injection
    )
    with pytest.raises(RuntimeError, match="來源.*(變更|重新抽取)"):
        kubejs_translator.run_kubejs_pipeline(
            input_dir=str(patch_script.parents[2]),
            output_dir=str(output),
            source_mode="incremental",
            translator_fn=_fake_translate_pending,
        )

    current_manifest = json.loads(
        (output / "kubejs" / ".pipeline" / "current.json").read_text(encoding="utf-8")
    )
    assert current_manifest["stage"] == "translated"
    assert old_final.read_bytes() == old_final_bytes


def test_incremental_cn_priority_end_to_end_step1_then_resume_step2_step3(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Output"
    lang = tmp_path / "pack" / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    english = {"apple": "Apple"}
    (lang / "en_us.json").write_text(json.dumps(english), encoding="utf-8")
    (lang / "zh_tw.json").write_text(
        json.dumps({"apple": "蘋果"}, ensure_ascii=False), encoding="utf-8"
    )

    fresh = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        step_translate=False,
        step_inject=False,
    )
    (lang / "zh_cn.json").write_text(
        json.dumps({"apple": "香蕉"}, ensure_ascii=False), encoding="utf-8"
    )
    incremental_add = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )
    (lang / "zh_cn.json").write_text(
        json.dumps({"apple": "葡萄"}, ensure_ascii=False), encoding="utf-8"
    )
    incremental_update = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )

    expected_tw = {"apple": "蘋果"}
    for result in (fresh, incremental_add, incremental_update):
        final_lang = (
            _private_snapshot_layer(result, output, "final_snapshot", "完成/kubejs")
            / "assets"
            / "demo"
            / "lang"
            / "zh_tw.json"
        )
        assert json.loads(final_lang.read_text(encoding="utf-8")) == expected_tw

    english["pear"] = "Pear"
    (lang / "en_us.json").write_text(json.dumps(english), encoding="utf-8")
    step1_only = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        source_mode="incremental",
        step_translate=False,
        step_inject=False,
    )
    pending_lang = (
        _private_snapshot_layer(step1_only, output, "pending_snapshot", "待翻譯/kubejs")
        / "assets"
        / "demo"
        / "lang"
        / "en_us.json"
    )
    assert json.loads(pending_lang.read_text(encoding="utf-8")) == {"pear": "Pear"}

    resumed = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(lang.parents[3]),
        output_dir=str(output),
        step_extract=False,
        step_translate=True,
        step_inject=True,
        translator_fn=_fake_translate_pending,
    )
    final_root = _private_snapshot_layer(
        resumed, output, "final_snapshot", "完成/kubejs"
    )
    final_lang = json.loads(
        (final_root / "assets" / "demo" / "lang" / "zh_tw.json").read_text(
            encoding="utf-8"
        )
    )
    assert final_lang == {"apple": "蘋果", "pear": "譯:Pear"}
    manifest = json.loads(
        (output / "kubejs" / ".pipeline" / "current.json").read_text(encoding="utf-8")
    )
    source = manifest["sources"][0]
    assert source["fingerprint"]
    source_raw = Path(resumed["paths"]["snapshot"]) / source["snapshot"]
    source_tw = json.loads(
        (source_raw / "assets" / "demo" / "lang" / "zh_tw.json").read_text(
            encoding="utf-8"
        )
    )
    source_cn = json.loads(
        (source_raw / "assets" / "demo" / "lang" / "zh_cn.json").read_text(
            encoding="utf-8"
        )
    )
    assert source_tw == {"apple": "蘋果"}
    assert source_cn == {"apple": "葡萄"}
    assert json.loads(
        (
            Path(resumed["paths"]["pending"])
            / "assets"
            / "demo"
            / "lang"
            / "en_us.json"
        ).read_text(encoding="utf-8")
    ) == {"pear": "Pear"}
    translated_root = (
        Path(resumed["paths"]["snapshot"]) / manifest["translated_snapshot"]
    )
    assert json.loads(
        (translated_root / "assets" / "demo" / "lang" / "zh_tw.json").read_text(
            encoding="utf-8"
        )
    ) == {"pear": "譯:Pear"}
    assert (
        json.loads(
            (
                Path(resumed["paths"]["final"])
                / "assets"
                / "demo"
                / "lang"
                / "zh_tw.json"
            ).read_text(encoding="utf-8")
        )
        == final_lang
    )
    assert resumed["step3"]["patched_js_files"] == 0
    assert resumed["mirror_conflict_count"] == 0
    assert resumed["output_sync_status"] == "synced"
