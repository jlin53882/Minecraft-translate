from __future__ import annotations

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


def test_run_kubejs_pipeline_skips_step2_when_no_pending_keys(tmp_path: Path, monkeypatch) -> None:
    session = _FakeSession()

    monkeypatch.setattr(
        kubejs_translator,
        "step1_extract_and_clean",
        lambda **kwargs: {"pending_dir": str(tmp_path / "Output" / "kubejs" / "待翻譯" / "kubejs")},
    )
    monkeypatch.setattr(kubejs_translator, "step3_inject", lambda **kwargs: {"ok": True})

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
