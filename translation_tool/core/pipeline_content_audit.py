"""Classify non-language content before the bundle step.

The merge stage historically kept content that had no locale directory under
``other_output/assets``.  That is useful as a diagnostic area, but it is not a
pipeline destination: translated text must be emitted to canonical ``assets``
and English guides must enter the normal guide translator.  This module keeps
that boundary explicit and leaves unsupported formats pending for a future
format profile instead of silently dropping them.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from translation_tool.core.lang_merge_dict import contains_cjk

_TEXT_SUFFIXES = frozenset({".md", ".txt"})
_LOCALE_DIRS = frozenset({"en_us", "_en_us", "zh_tw", "_zh_tw"})


def _load_existing_content_records(report_path: Path) -> list[dict[str, Any]]:
    """讀取同一個 pipeline run 先前已分類的 provenance。"""
    if not report_path.is_file():
        return []
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        return []
    return [
        record
        for record in payload["records"]
        if isinstance(record, dict) and record.get("source")
    ]


def _is_live_content_record(root: Path, record: dict[str, Any]) -> bool:
    """保留仍有來源或 canonical/pending 輸出的舊紀錄，淘汰過期紀錄。"""
    source = Path(str(record["source"]))
    if source.is_file():
        return True
    output = str(record.get("output", ""))
    if not output:
        return False
    candidate = root / Path(output)
    try:
        return (
            candidate.resolve().is_relative_to(root.resolve()) and candidate.is_file()
        )
    except OSError:
        return False


def _merge_content_records(
    root: Path, report_path: Path, current: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """合併同一輸出目錄多個 merge 階段的紀錄，避免後一階段覆蓋前一階段。"""
    merged: dict[str, dict[str, Any]] = {}
    for record in _load_existing_content_records(report_path):
        if _is_live_content_record(root, record):
            merged[str(record["source"])] = record
    for record in current:
        merged[str(record["source"])] = record
    return sorted(merged.values(), key=lambda item: str(item["source"]).casefold())


def _write_content_manifest(report_path: Path, records: list[dict[str, Any]]) -> None:
    """以穩定格式寫入內容分類 provenance。"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "records": records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _pending_guide_rel(relative: Path) -> Path:
    """Add a synthetic English locale only when the source has no locale slot."""

    parts = list(relative.parts)
    if any(part.lower() in _LOCALE_DIRS for part in parts[2:-1]):
        return relative
    if len(parts) < 2:
        return relative
    return Path(*parts[:-1], "_en_us", parts[-1])


def _copy_if_needed(source: Path, destination: Path) -> bool:
    """Copy one generated output, returning whether the destination changed."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        try:
            if source.read_bytes() == destination.read_bytes():
                return False
        except OSError:
            pass
    shutil.copy2(source, destination)
    return True


def classify_other_output_assets(
    merge_output_dir: str | Path,
    *,
    pending_folder_name: str = "待翻譯",
    content_manifest_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Move known content to an explicit emitted or pending pipeline path.

    Returns one audit record per source file.  ``.md``/``.txt`` containing CJK
    are already localized and are emitted at their original resource path.
    English text is copied under a synthetic ``_en_us`` directory so the
    normal guide scanner and writer can translate it to ``_zh_tw``.  Other
    formats remain under ``other_output`` and are marked pending because no
    verified schema exists for them yet.
    """

    root = Path(merge_output_dir)
    source_root = root / "other_output" / "assets"

    canonical_root = root / "assets"
    pending_root = root / "patchouli_output" / pending_folder_name
    records: list[dict[str, Any]] = []

    for source in sorted(
        (path for path in source_root.rglob("*") if path.is_file()),
        key=lambda path: path.as_posix().casefold(),
    ):
        relative = source.relative_to(source_root)
        record: dict[str, Any] = {
            "source": source.as_posix(),
            "relative_path": Path("assets", relative).as_posix(),
        }
        if source.suffix.lower() not in _TEXT_SUFFIXES:
            record.update(
                status="pending",
                reason="unsupported_other_output_format",
            )
            records.append(record)
            continue

        try:
            text = source.read_text(encoding="utf-8-sig")
            if contains_cjk(text):
                destination = canonical_root / relative
                _copy_if_needed(source, destination)
                source.unlink()
                record.update(
                    status="emitted",
                    output=destination.relative_to(root).as_posix(),
                    reason="localized_text_preserved",
                )
            else:
                destination = pending_root / _pending_guide_rel(
                    Path("assets", relative)
                )
                _copy_if_needed(source, destination)
                source.unlink()
                record.update(
                    status="pending",
                    output=destination.relative_to(root).as_posix(),
                    reason="english_text_queued_for_guide_translation",
                )
        except (OSError, UnicodeError) as exc:
            record.update(
                status="parse_failed",
                reason=f"other_output_read_failed:{type(exc).__name__}",
            )
        records.append(record)

    report_path = (
        Path(content_manifest_path)
        if content_manifest_path is not None
        else root.parent / "translation_content_manifest.json"
    )
    _write_content_manifest(
        report_path,
        _merge_content_records(root, report_path, records),
    )
    return records


__all__ = ["classify_other_output_assets"]
