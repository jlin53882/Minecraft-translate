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

    monkeypatch.setattr(
        kubejs_translator,
        "step1_extract_and_clean",
        lambda **kwargs: {
            "pending_dir": str(tmp_path / "Output" / "kubejs" / "待翻譯" / "kubejs")
        },
    )
    monkeypatch.setattr(
        kubejs_translator, "step3_inject", lambda **kwargs: {"ok": True}
    )

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
    assert result["step2"]["reason"] == "pending lang keys = 0"
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
