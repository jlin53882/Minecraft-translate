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
import re
import shutil
from pathlib import Path
from typing import Any

from translation_tool.core.guide_blocks import (
    extract_guide_blocks,
    extract_meta_entries,
    has_translatable_text,
)
from translation_tool.core.guide_translation import (
    guide_locale_rel,
    normalize_guideme_default_locale_rel,
)
from translation_tool.core.lang_merge_dict import contains_cjk
from translation_tool.core.locale_path import (
    KIND_NAME,
    find_locale_marker,
    has_locale_dir,
)
from translation_tool.core.oracle_index import is_translatable_meta, split_translated

_TEXT_SUFFIXES = frozenset({".md", ".mdx", ".txt"})
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")


def _is_text_source(relative: Path) -> bool:
    """進指南翻譯流程的文字檔：``.md``／``.mdx``／``.txt``，以及 Oracle Index 書的選單 ``_meta.json``。"""
    if relative.suffix.lower() in _TEXT_SUFFIXES:
        return True
    return is_translatable_meta(Path("assets", relative).as_posix())


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


def _needs_guide_translation(text: str, name: str) -> bool:
    """保守判斷指南是否仍含英文或中英混合的可翻譯區塊。

    文件內只要有一個可翻譯的純英文區塊，或可翻譯區塊同時含中文與
    拉丁字詞，就不能宣稱整份文件已完成；應交給指南翻譯流程逐區塊處理。
    """
    for block in extract_guide_blocks(text, name):
        if not has_translatable_text(block.text):
            continue
        if not contains_cjk(block.text) or _LATIN_WORD.search(block.text):
            return True
    return False


def _pending_guide_rel(relative: Path, root: Path | None = None) -> Path:
    """Add a synthetic English locale only when the source has no locale slot."""

    parts = list(relative.parts)
    if has_locale_dir(parts, "en_us", 2) or has_locale_dir(parts, "zh_tw", 2):
        return relative
    if len(parts) < 2:
        return relative
    # Use the same guide-wide locale slot as the final writer.  In particular,
    # ``assets/mod/ae2guide/chapter/page.md`` becomes
    # ``assets/mod/ae2guide/_en_us/chapter/page.md`` rather than placing the
    # locale below ``chapter``.  The latter makes every relative link resolve
    # against a different tree and was the source of the in-game "Page does
    # not exist" errors.
    return guide_locale_rel(relative, root or Path(), "_en_us")


def _is_official_translation(relative: Path, text: str) -> bool:
    """模組自帶、可以直接保留的繁中譯文。

    路徑要帶繁中語系記號（``_zh_tw/``、``zh_tw_0/``、``<名稱>_zh_tw.txt``），內容也要含中文：
    有些模組在 ``_zh_tw`` 放未翻譯的英文備用頁，那種檔案不能算官方譯文，對應的英文原件仍要送翻譯。
    """

    marker = find_locale_marker(relative.parts, "zh_tw")
    return marker is not None and marker.kind != KIND_NAME and contains_cjk(text)


def _is_incomplete_oracle_menu(relative: Path, text: str, source_root: Path) -> bool:
    """Oracle Index 的譯文選單少了原文選單的鍵。

    譯文選單會整份取代原文，缺的鍵在遊戲裡直接消失，所以不能當作完整的官方譯文保留；
    原文選單要留下來送翻譯，由翻譯流程補齊每個鍵。
    """

    assets_rel = Path("assets", relative)
    if not is_translatable_meta(assets_rel.as_posix()):
        return False
    split = split_translated(assets_rel.parts)
    if split is None:
        return False
    original = source_root.parent / split[1]
    if not original.is_file():
        return False
    try:
        translated = extract_meta_entries(text)
        source = extract_meta_entries(original.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError):
        return False
    if translated is None or source is None:
        return False
    return not {key for key, _ in source} <= {key for key, _ in translated}


def _incomplete_oracle_menus(source_root: Path) -> set[str]:
    """``other_output`` 內不完整的 Oracle 譯文選單（相對 ``assets`` 的比對鍵）。

    和官方譯文鍵一樣要在分類開始前算好：分類會把處理完的原文選單移走，之後就比對不到鍵了。
    """

    found: set[str] = set()
    for path in source_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source_root)
        if not _is_text_source(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        if _is_incomplete_oracle_menu(relative, text, source_root):
            found.add(Path("assets", relative).as_posix().casefold())
    return found


def _official_translation_keys(source_root: Path) -> set[str]:
    """``other_output`` 內模組自帶、可直接保留的繁中指南／文字檔（相對 ``assets`` 的比對鍵）。

    必須在分類開始前一次算好：分類過程會把處理完的來源移走，逐檔檢查會受處理順序影響。
    判斷條件與 ``_route_text_source`` 保留官方譯文的條件相同，兩邊不一致時英文原件
    會被略過、官方檔又沒輸出，兩者都不會進資源包。
    """

    keys: set[str] = set()
    for path in source_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source_root)
        if not _is_text_source(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue  # 讀不出來的檔案由分類迴圈記為 parse_failed
        if _is_official_translation(relative, text) and not _is_incomplete_oracle_menu(
            relative, text, source_root
        ):
            keys.add(Path("assets", relative).as_posix().casefold())
    return keys


def _official_counterpart_key(relative: Path, root: Path) -> str:
    """英文原文對應的繁中檔比對鍵（與翻譯輸出位置相同的規則）。"""

    target = normalize_guideme_default_locale_rel(
        guide_locale_rel(relative, root, "_zh_tw")
    )
    return target.as_posix().casefold()


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


def _route_text_source(
    source: Path,
    relative: Path,
    *,
    root: Path,
    scan_root: Path,
    pending_root: Path,
    official_keys: set[str],
    incomplete_menus: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """決定一個 ``other_output`` 文字檔的去向，回傳 manifest 欄位；處理完的來源會被移走。

    譯文來源的優先序：模組自帶的繁中 > 簡中轉繁（語系整理已直接寫進 ``assets``）；
    模組已有繁中時，英文原件不再送 LM 翻譯，避免機器譯文蓋掉官方譯文。
    """

    text = source.read_text(encoding="utf-8-sig")
    assets_rel = Path("assets", relative)
    if assets_rel.as_posix().casefold() in incomplete_menus:
        source.unlink()
        return {"status": "excluded", "reason": "oracle_menu_translation_incomplete"}
    official = _is_official_translation(relative, text)
    if official or (
        contains_cjk(text) and not _needs_guide_translation(text, source.name)
    ):
        destination = root / assets_rel
        reason = (
            "official_translation_preserved" if official else "localized_text_preserved"
        )
        status = "emitted"
    elif _official_counterpart_key(assets_rel, scan_root) in official_keys:
        source.unlink()
        return {
            "status": "excluded",
            "reason": "english_text_has_official_translation",
        }
    else:
        destination = pending_root / _pending_guide_rel(assets_rel, scan_root)
        reason = "english_text_queued_for_guide_translation"
        status = "pending"
    _copy_if_needed(source, destination)
    source.unlink()
    return {
        "status": status,
        "output": destination.relative_to(root).as_posix(),
        "reason": reason,
    }


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

    pending_root = root / "patchouli_output" / pending_folder_name
    official_keys = _official_translation_keys(source_root)
    incomplete_menus = frozenset(_incomplete_oracle_menus(source_root))
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
        if not _is_text_source(relative):
            record.update(
                status="pending",
                reason="unsupported_other_output_format",
            )
            records.append(record)
            continue

        try:
            record.update(
                _route_text_source(
                    source,
                    relative,
                    root=root,
                    scan_root=source_root.parent,
                    pending_root=pending_root,
                    official_keys=official_keys,
                    incomplete_menus=incomplete_menus,
                )
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
