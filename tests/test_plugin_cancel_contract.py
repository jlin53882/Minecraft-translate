"""FTB／MD／KubeJS 取消語義契約：取消後不得 inject、不得偽稱完成，且翻譯輸出保留。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from translation_tool.core import (
    ftb_translator,
    kubejs_translator,
    md_translation_assembly,
)
from translation_tool.plugins.ftbquests import ftbquests_lmtranslator
from translation_tool.plugins.md import md_lmtranslator
from translation_tool.utils.cancellation import cancel_scope


def _cancelled_loop(*args, **kwargs):
    """模擬共用迴圈被取消：回傳 CANCELLED、不丟例外。"""
    return SimpleNamespace(
        status="CANCELLED", processed=0, elapsed_sec=0.0, completed_calls=0
    )


def _make_ftb_pack(root: Path) -> None:
    quests_root = root / "config" / "ftbquests" / "quests"
    lang_root = quests_root / "lang"
    lang_root.mkdir(parents=True)
    (lang_root / "en_us.snbt").write_text(
        '{ quest.title: "English title" }', encoding="utf-8"
    )
    (quests_root / "chapter.snbt").write_text(
        '{ id: "quest.one", title: "Quest title" }', encoding="utf-8"
    )


# ---------------------------------------------------------------- FTB


def test_ftb_plugin_stops_after_cancelled_loop_and_reports_status(
    tmp_path: Path, monkeypatch
) -> None:
    """FTB 迴圈對 CANCELLED 也要 break，不處理後續檔案，且回傳值標示 cancelled。"""
    in_dir = tmp_path / "lang"
    (in_dir / "en_us").mkdir(parents=True)
    for name in ("a", "b"):
        (in_dir / "en_us" / f"{name}.json").write_text(
            json.dumps({f"{name}.key": f"Text {name}"}), encoding="utf-8"
        )
    calls: list[int] = []

    def fake_loop(*args, **kwargs):
        calls.append(1)
        return _cancelled_loop()

    monkeypatch.setattr(ftbquests_lmtranslator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(ftbquests_lmtranslator, "run_translator_skeleton", fake_loop)

    result = ftbquests_lmtranslator.translate_ftb_pending_to_zh_tw(
        input_lang_dir=str(in_dir),
        output_lang_dir=str(tmp_path / "out"),
        dry_run=False,
        write_new_cache=False,
    )

    assert len(calls) == 1
    assert result["status"] == "CANCELLED"
    assert result["cancelled"] is True


@pytest.mark.parametrize("status", ["PARTIAL", "FAILED"])
def test_ftb_plugin_stops_after_non_done_loop_and_reports_status(
    status: str, tmp_path: Path, monkeypatch
) -> None:
    """FTB 對部分完成或失敗不得繼續處理後續檔案，也不得回報 DONE。"""
    in_dir = tmp_path / "lang"
    (in_dir / "en_us").mkdir(parents=True)
    for name in ("a", "b"):
        (in_dir / "en_us" / f"{name}.json").write_text(
            json.dumps({f"{name}.key": f"Text {name}"}), encoding="utf-8"
        )
    calls: list[int] = []

    def fake_loop(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(
            status=status, processed=0, elapsed_sec=0.0, completed_calls=0
        )

    monkeypatch.setattr(ftbquests_lmtranslator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(ftbquests_lmtranslator, "run_translator_skeleton", fake_loop)

    result = ftbquests_lmtranslator.translate_ftb_pending_to_zh_tw(
        input_lang_dir=str(in_dir),
        output_lang_dir=str(tmp_path / "out"),
        dry_run=False,
        write_new_cache=False,
    )

    assert len(calls) == 1
    assert result["status"] == status
    assert result["cancelled"] is False


def test_ftb_pipeline_skips_inject_when_translation_cancelled(
    tmp_path: Path, monkeypatch
) -> None:
    _make_ftb_pack(tmp_path)

    def fake_translate(*, input_lang_dir: str, output_lang_dir: str, **kwargs) -> dict:
        target = Path(output_lang_dir) / "zh_tw"
        target.mkdir(parents=True, exist_ok=True)
        (target / "ftb_lang.json").write_text(
            '{"quest.title": "部分譯文"}', encoding="utf-8"
        )
        return {"status": "CANCELLED", "cancelled": True, "written_files": 1}

    monkeypatch.setattr(
        ftbquests_lmtranslator, "translate_ftb_pending_to_zh_tw", fake_translate
    )

    result = ftb_translator.run_ftb_pipeline(
        directory_path=str(tmp_path),
        output_dir=str(tmp_path / "Output"),
        dry_run=False,
    )

    assert result["cancelled"] is True
    assert result["inject"] == {"skipped": True, "reason": "cancelled"}
    assert not (tmp_path / "Output" / "ftbquests" / "完成").exists()
    # 已完成的翻譯輸出保留
    kept = tmp_path / "Output" / "ftbquests" / "LM翻譯輸出"
    assert list(kept.rglob("ftb_lang.json"))


@pytest.mark.parametrize(
    ("translation_result", "expected_reason"),
    [
        ({"status": "PARTIAL", "cancelled": False}, "translation_incomplete"),
        ({"status": "FAILED", "cancelled": False}, "translation_failed"),
        ({}, "translation_status_unknown"),
        ({"status": ""}, "translation_status_unknown"),
        ({"status": "UNKNOWN"}, "translation_status_unknown"),
    ],
)
def test_ftb_pipeline_skips_inject_when_translation_is_not_explicitly_done(
    translation_result: dict, expected_reason: str, tmp_path: Path, monkeypatch
) -> None:
    """FTB 只有明確 DONE 才能注入，缺少或未知狀態也必須停止。"""
    _make_ftb_pack(tmp_path)

    def fake_translate(*, input_lang_dir: str, output_lang_dir: str, **kwargs) -> dict:
        target = Path(output_lang_dir) / "zh_tw"
        target.mkdir(parents=True, exist_ok=True)
        (target / "ftb_lang.json").write_text(
            '{"quest.title": "部分譯文"}', encoding="utf-8"
        )
        result = dict(translation_result)
        result["written_files"] = 1
        return result

    monkeypatch.setattr(
        ftbquests_lmtranslator, "translate_ftb_pending_to_zh_tw", fake_translate
    )

    result = ftb_translator.run_ftb_pipeline(
        directory_path=str(tmp_path),
        output_dir=str(tmp_path / "Output"),
        dry_run=False,
    )

    assert result["inject"] == {
        "skipped": True,
        "reason": expected_reason,
    }
    assert not (tmp_path / "Output" / "ftbquests" / "完成").exists()


def test_ftb_pipeline_skips_inject_when_cancel_requested_before_inject(
    tmp_path: Path,
) -> None:
    """只執行 inject 時若已要求取消，也不啟動 inject。"""
    _make_ftb_pack(tmp_path)
    with cancel_scope(lambda: True):
        result = ftb_translator.run_ftb_pipeline(
            directory_path=str(tmp_path),
            output_dir=str(tmp_path / "Output"),
            step_export=False,
            step_clean=False,
            step_translate=False,
            step_inject=True,
        )
    assert result["inject"]["reason"] == "cancelled"
    assert not (tmp_path / "Output" / "ftbquests" / "完成").exists()


def test_ftb_inject_only_not_blocked_without_cancel(tmp_path: Path) -> None:
    """未取消時只執行 inject 照常進入（缺 zh_tw JSON 才會是 FileNotFoundError，而非被誤擋）。"""
    _make_ftb_pack(tmp_path)
    with pytest.raises(FileNotFoundError):
        ftb_translator.run_ftb_pipeline(
            directory_path=str(tmp_path),
            output_dir=str(tmp_path / "Output"),
            step_export=False,
            step_clean=False,
            step_translate=False,
            step_inject=True,
        )


# ---------------------------------------------------------------- MD


def _make_md_pack(root: Path) -> None:
    doc = root / "docs" / "en_us"
    doc.mkdir(parents=True)
    (doc / "intro.md").write_text("Hello world\n", encoding="utf-8")


def test_md_plugin_reports_cancelled_status(tmp_path: Path, monkeypatch) -> None:
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "intro.md.json").write_text(
        json.dumps(
            {
                "schema": "md_pending_blocks_v1",
                "items": [
                    {
                        "id": "1",
                        "text": "Hello brave new world",
                        "content_hash": "h1",
                        "start_line": 1,
                        "end_line": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(md_lmtranslator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(md_lmtranslator, "run_translator_skeleton", _cancelled_loop)

    result = md_lmtranslator.translate_md_pending(
        pending_dir=str(pending),
        out_dir=str(tmp_path / "out"),
        write_new_cache=False,
    )

    assert result["status"] == "CANCELLED"
    assert result["cancelled"] is True


def test_md_pipeline_does_not_inject_old_pending_after_cancel(
    tmp_path: Path, monkeypatch
) -> None:
    _make_md_pack(tmp_path)

    def fake_translate(**kwargs):
        return {"status": "CANCELLED", "cancelled": True, "written_files": 0}

    monkeypatch.setattr(md_translation_assembly, "translate_md_pending", fake_translate)

    result = md_translation_assembly.run_md_pipeline(
        input_dir=str(tmp_path),
        output_dir=str(tmp_path / "Output"),
        lang_mode="all",
    )

    assert result["cancelled"] is True
    assert result["step3"] == {"skipped": True, "reason": "cancelled"}
    final_dir = tmp_path / "Output" / "md" / "完成"
    assert not list(final_dir.rglob("*.md"))
    # 待翻譯 JSON（續跑所需）保留
    assert list((tmp_path / "Output" / "md" / "待翻譯").rglob("*.json"))


@pytest.mark.parametrize(
    ("translation_result", "expected_reason"),
    [
        ({"status": "PARTIAL", "cancelled": False}, "translation_incomplete"),
        ({"status": "FAILED", "cancelled": False}, "translation_failed"),
        ({}, "translation_status_unknown"),
        ({"status": ""}, "translation_status_unknown"),
        ({"status": "UNKNOWN"}, "translation_status_unknown"),
    ],
)
def test_md_pipeline_does_not_inject_after_non_done_translation(
    translation_result: dict,
    expected_reason: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Markdown 只有明確 DONE 才能使用翻譯 JSON 回寫原始檔案。"""
    _make_md_pack(tmp_path)

    def fake_translate(**kwargs):
        return dict(translation_result)

    monkeypatch.setattr(md_translation_assembly, "translate_md_pending", fake_translate)

    result = md_translation_assembly.run_md_pipeline(
        input_dir=str(tmp_path),
        output_dir=str(tmp_path / "Output"),
        lang_mode="all",
    )

    assert result["step3"] == {
        "skipped": True,
        "reason": expected_reason,
    }
    assert not list((tmp_path / "Output" / "md" / "完成").rglob("*.md"))


def test_md_inject_only_not_blocked_without_cancel(tmp_path: Path) -> None:
    """只執行 inject（step_translate 關閉）且未取消時照常回寫。"""
    _make_md_pack(tmp_path)
    result = md_translation_assembly.run_md_pipeline(
        input_dir=str(tmp_path),
        output_dir=str(tmp_path / "Output"),
        step_translate=False,
        lang_mode="all",
    )
    assert "cancelled" not in result
    assert result["step3"].get("reason") != "cancelled"


# ---------------------------------------------------------------- KubeJS


def test_kubejs_pipeline_cancelled_result_from_translator_not_committed(
    tmp_path: Path,
) -> None:
    """KubeJS 對照組：翻譯器回報 cancelled 時不提交快照、不 inject。"""
    kubejs = tmp_path / "kubejs" / "client_scripts"
    kubejs.mkdir(parents=True)
    (kubejs / "tooltip.js").write_text(
        "scene.text('scene', 'Text')\n", encoding="utf-8"
    )

    def cancelled_translator(**kwargs):
        return {"status": "CANCELLED", "cancelled": True}

    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(kubejs.parent),
        output_dir=str(tmp_path / "Output"),
        translator_fn=cancelled_translator,
        step_inject=True,
    )

    assert result["step2"]["reason"] == "cancelled"
    assert result["step3"] == {"skipped": True, "reason": "cancelled"}


@pytest.mark.parametrize(
    ("translation_result", "expected_reason"),
    [
        ({"status": "PARTIAL", "cancelled": False}, "translation_incomplete"),
        ({"status": "FAILED", "cancelled": False}, "translation_failed"),
        ({}, "translation_status_unknown"),
        ({"status": ""}, "translation_status_unknown"),
        ({"status": "UNKNOWN"}, "translation_status_unknown"),
    ],
)
def test_kubejs_pipeline_non_done_result_is_not_committed_or_injected(
    translation_result: dict, expected_reason: str, tmp_path: Path
) -> None:
    """KubeJS 只有明確 DONE 才能提交翻譯快照與執行注入。"""
    kubejs = tmp_path / "kubejs" / "client_scripts"
    kubejs.mkdir(parents=True)
    (kubejs / "tooltip.js").write_text(
        "scene.text('scene', 'Text')\n", encoding="utf-8"
    )

    def incomplete_translator(**kwargs):
        return dict(translation_result)

    result = kubejs_translator.run_kubejs_pipeline(
        input_dir=str(kubejs.parent),
        output_dir=str(tmp_path / "Output"),
        translator_fn=incomplete_translator,
        step_inject=True,
    )

    expected_step2 = dict(translation_result)
    expected_step2.update({"skipped": True, "reason": expected_reason})
    assert result["step2"] == expected_step2
    assert result["step3"] == {
        "skipped": True,
        "reason": expected_reason,
    }
