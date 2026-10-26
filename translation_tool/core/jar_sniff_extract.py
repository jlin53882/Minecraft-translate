"""jar_sniff_extract.py

提取資料夾名稱沒對上的手冊／指南檔（依內容辨識，不需要設定資料夾名稱）。

``jar_processor`` 的 Book 提取只認得資料夾名稱設定；這裡在它之後補一輪：
讀每個 jar 的檔案內容，用 ``translation_db.sniff_groups`` 同一套判斷找出手冊 JSON 與指南
（``.md``／``.txt``），把原文與要提取的語系變體寫到同一個輸出資料夾，路徑慣例與
``jar_processor_extract`` 相同（``assets/`` 底下照原路徑，其他放進 ``<模組>_extracted/``）。
"""

from __future__ import annotations

import os
import zipfile
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from translation_tool.core.jar_processor_extract import (
    _normalize_jar_base_name,
    build_nested_output_names,
    get_file_hash,
)
from translation_tool.translation_db.schema import KIND_GUIDE, KIND_PATCHOULI
from translation_tool.translation_db.sniff_groups import sniffed_groups_detailed
from translation_tool.utils.cancellation import raise_if_cancelled
from translation_tool.utils.log_unit import log_error, log_warning
from translation_tool.utils.nested_archive import (
    is_nested_archive_name,
    walk_nested_archives,
)
from translation_tool.utils.zip_safety import (
    MAX_FILE_BYTES,
    ArchiveBudgetError,
    UnsafePathError,
    ZipReadBudget,
    ZipSizeError,
    read_limited,
    safe_join,
)

_BOOK_KINDS = (KIND_PATCHOULI, KIND_GUIDE)


@dataclass
class SniffTraversalStatus:
    """補掃結果的完整性狀態；部分結果仍可供呼叫端使用。"""

    incomplete: bool = False
    reasons: list[str] = field(default_factory=list)

    def add_reason(self, reason: str) -> None:
        self.incomplete = True
        if reason and reason not in self.reasons:
            self.reasons.append(reason)


def _wanted_langs(lang_codes: list[str], skip_zh_cn: bool) -> set[str]:
    langs = {"en_us"}
    langs.update(c for c in lang_codes if c in ("zh_tw", "zh_cn"))
    if skip_zh_cn:
        langs.discard("zh_cn")
    return langs


def _output_path(
    output_root: str,
    jar_base: str,
    member: str,
    *,
    jar_stem: str | None = None,
    is_nested: bool = False,
    nested_output_name: str | None = None,
) -> str:
    if is_nested:
        return safe_join(
            output_root,
            nested_output_name or f"{jar_stem or jar_base}_extracted",
            member,
        )
    if member.startswith("assets/"):
        return safe_join(output_root, member)
    return safe_join(output_root, f"{jar_base}_extracted", member)


def _walk_sniffed_members(
    jar_path: str,
    claimed: Callable[[str], bool],
    langs: set[str],
    on_member: Callable[
        [zipfile.ZipFile, str, str, tuple[str, ...], ZipReadBudget], None
    ],
) -> SniffTraversalStatus:
    """在 outer／nested archive 中找出未被路徑規則認領的書籍／指南成員。

    DB scanner 與 Book extraction 必須使用同一個 ``sniffed_groups`` 判斷。
    這裡保留 archive 仍開啟時執行的 callback，讓提取端可以安全讀取 nested
    member，也讓預覽端只取檔案大小而不必把內容留在記憶體。
    """
    jar_name = os.path.basename(jar_path)
    budget = ZipReadBudget(label=jar_name)
    seen_members: set[str] = set()
    status = SniffTraversalStatus()

    def visit_archive(
        current: zipfile.ZipFile,
        label: str,
        _depth: int,
        archive_chain: tuple[str, ...],
        current_budget: ZipReadBudget,
    ) -> bool:
        names = current.namelist()
        taken = {name for name in names if claimed(name)}
        diagnostics: list[str] = []
        sniff_result = sniffed_groups_detailed(
            current,
            names,
            taken,
            diagnostics=diagnostics,
        )
        if sniff_result.budget_exhausted:
            status.add_reason("Book sniff 讀取預算耗盡：" + sniff_result.budget_error)
        groups = sniff_result.groups
        for member in dict.fromkeys(diagnostics):
            log_warning("[Sniff] 略過無法解析的書籍 JSON：%s（來源 %s）", member, label)
        for (kind, _mod, _key), members in groups.items():
            if kind not in _BOOK_KINDS:
                continue
            for lang, member in members.items():
                if lang in langs and member not in seen_members:
                    seen_members.add(member)
                    on_member(current, member, kind, archive_chain, current_budget)
        return True

    def on_nested_error(label: str, name: str, exc: BaseException) -> None:
        status.add_reason(f"略過內嵌 jar {label}：{exc}")
        log_warning("[Sniff] 略過內嵌 jar %s：%s", label, exc)

    try:
        with zipfile.ZipFile(jar_path) as zf:
            walk_nested_archives(
                zf,
                jar_name,
                budget,
                visit_archive,
                budget_factory=lambda _label: budget,
                on_nested_error=on_nested_error,
                nested_name_predicate=is_nested_archive_name,
            )
    except ArchiveBudgetError as exc:
        status.add_reason(str(exc))
    return status


def extract_sniffed_from_jar(
    jar_path: str,
    output_root: str,
    claimed: Callable[[str], bool],
    langs: set[str],
    *,
    nested_output_name: str | None = None,
) -> tuple[int, int]:
    """回傳 ``(寫出, 略過)``。``claimed(名稱)`` 為真的檔案已由名稱規則提取，不重複處理。"""
    written, skipped, status = _extract_sniffed_from_jar_detailed(
        jar_path,
        output_root,
        claimed,
        langs,
        nested_output_name=nested_output_name,
    )
    if status.incomplete:
        log_warning(
            "[Sniff] %s 僅完成部分補掃：%s",
            jar_path,
            "；".join(status.reasons),
        )
    return written, skipped


def _extract_sniffed_from_jar_detailed(
    jar_path: str,
    output_root: str,
    claimed: Callable[[str], bool],
    langs: set[str],
    *,
    nested_output_name: str | None = None,
    direct_asset_winners: dict[str, Path] | None = None,
) -> tuple[int, int, SniffTraversalStatus]:
    """供 pipeline 使用的補掃結果，保留寫入數與完整性狀態。"""
    written = skipped = 0
    jar_base = _normalize_jar_base_name(jar_path)
    jar_identity = Path(jar_path).resolve()

    def write_member(
        zf: zipfile.ZipFile,
        member: str,
        _kind: str,
        archive_chain: tuple[str, ...],
        budget: ZipReadBudget,
    ) -> None:
        nonlocal written, skipped
        if (
            direct_asset_winners is not None
            and not archive_chain
            and member.startswith("assets/")
        ):
            owner = direct_asset_winners.setdefault(member, jar_identity)
            if owner != jar_identity:
                log_warning(
                    "[Sniff] direct assets 路徑衝突，保留先出現來源：%s（略過 %s）",
                    member,
                    jar_path,
                )
                skipped += 1
                return
        try:
            target = _output_path(
                output_root,
                jar_base,
                member,
                jar_stem=Path(jar_path).stem,
                is_nested=bool(archive_chain),
                nested_output_name=nested_output_name,
            )
            data = read_limited(zf, member, MAX_FILE_BYTES, budget=budget)
        except UnsafePathError:
            log_error("拒絕提取不安全路徑: %s（來源 %s）", member, jar_path)
            skipped += 1
            return
        except ArchiveBudgetError:
            raise
        except ZipSizeError as exc:
            log_error("略過過大檔案 %s: %s", member, exc)
            skipped += 1
            return
        if os.path.exists(target):
            with open(target, "rb") as existing:
                if get_file_hash(existing.read()) == get_file_hash(data):
                    skipped += 1
                    return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as out:
            out.write(data)
        written += 1

    status = _walk_sniffed_members(jar_path, claimed, langs, write_member)
    return written, skipped, status


def extract_sniffed_book_files(
    jar_paths: list[str],
    output_dir: str,
    claimed: Callable[[str], bool],
    *,
    lang_codes: list[str],
    skip_zh_cn: bool,
    source_root: str | Path | None = None,
) -> Generator[dict[str, Any], None, None]:
    """逐個 jar 補提取依內容辨識的手冊／指南，產生進度更新（只含 ``log``）。

    ``source_root`` must be the same root used by the regular extraction
    phase when both phases share an output directory.  Keeping the root
    explicit makes a nested JAR's source identity stable even when all JARs
    live below a common subdirectory.
    """
    langs = _wanted_langs(lang_codes, skip_zh_cn)
    nested_output_names = build_nested_output_names(jar_paths, source_root)
    direct_asset_winners: dict[str, Path] = {}
    total_written = 0
    for jar_path in jar_paths:
        raise_if_cancelled()
        try:
            written, _skipped, status = _extract_sniffed_from_jar_detailed(
                jar_path,
                output_dir,
                claimed,
                langs,
                nested_output_name=nested_output_names.get(Path(jar_path)),
                direct_asset_winners=direct_asset_winners,
            )
        except (zipfile.BadZipFile, OSError, ArchiveBudgetError) as exc:
            log_error("[Sniff] 無法讀取 %s：%s", jar_path, exc)
            continue
        if status.incomplete:
            yield {
                "log": (
                    f"[依內容辨識] {Path(jar_path).name}：部分完成；"
                    + "；".join(status.reasons)
                ),
                "level": "warning",
            }
        if written:
            total_written += written
            yield {"log": f"[依內容辨識] {Path(jar_path).name}：提取 {written} 個檔案"}
    yield {"log": f"[依內容辨識] 共補提取 {total_written} 個手冊／指南檔"}
