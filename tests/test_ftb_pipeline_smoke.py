from __future__ import annotations

from pathlib import Path

from translation_tool.core import ftb_translator


def test_run_ftb_pipeline_smoke_export_and_clean_only(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_export(directory_path: str, *, output_dir: str | None = None):
        calls.append(("export", output_dir))
        return {"raw_root": str(tmp_path / "Output" / "ftbquests" / "raw")}

    def fake_clean(directory_path: str, *, output_dir: str | None = None):
        calls.append(("clean", output_dir))
        pending_dir = tmp_path / "Output" / "ftbquests" / "待翻譯" / "config" / "ftbquests" / "quests" / "lang" / "en_us"
        pending_dir.mkdir(parents=True, exist_ok=True)
        return {
            "en_pending_dir": str(pending_dir),
            "zh_tw_dir": None,
        }

    monkeypatch.setattr(ftb_translator, "export_ftbquests_raw_json", fake_export)
    monkeypatch.setattr(ftb_translator, "clean_ftbquests_from_raw", fake_clean)

    result = ftb_translator.run_ftb_pipeline(
        str(tmp_path),
        output_dir=str(tmp_path / "Output"),
        step_export=True,
        step_clean=True,
        step_translate=False,
        step_inject=False,
    )

    assert [name for name, _ in calls] == ["export", "clean"]
    assert "raw_paths" in result
    assert "clean_paths" in result
