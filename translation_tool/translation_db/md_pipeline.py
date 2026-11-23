"""Markdown 指南的 ModDB 流水線：來源分析、DB 同步、LM 翻譯、輸出資源包 overlay。

與 KubeJS／FTB 流水線（``pack_pipeline``）同一個契約：預覽（dry-run）永遠只讀來源，
正式同步才寫 DB，發布才寫輸出資料夾；來源資料夾不會被修改。

條目身分沿用 jar 指南（``KIND_GUIDE``）：``assets|data/<模組>/…`` 底下的檔案用模組 ID，
其他資料夾用來源資料夾名稱組成的穩定 ID；鍵值是「中性路徑#段落序號」。
輸出檔案放在 ``<輸出>/<來源相對路徑，語言資料夾換成 zh_tw>``，來源是資源包結構時整個
輸出就是可放進 resourcepacks 的資料夾（附 ``pack.mcmeta``）。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from translation_tool.core.guide_blocks import (
    extract_guide_blocks,
    has_translatable_text,
)
from translation_tool.core.guide_translation import render_guide
from translation_tool.core.md_output_guard import MANIFEST_NAME, OutputGuard
from translation_tool.core.pack_format_lookup import resolve_version_input
from translation_tool.core.pack_mcmeta import build_pack_mcmeta
from translation_tool.core.text_sniffer import split_locale
from translation_tool.plugins.md.md_extract_qa import (
    contains_cjk,
    detect_lang_segment,
    iter_md_files,
    map_rel_lang_path,
)
from translation_tool.translation_db.models import IngestStats, ScanItem
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.resolver import TranslationResolver
from translation_tool.translation_db.scan_ingest import preview_ingest_many
from translation_tool.translation_db.schema import KIND_GUIDE
from translation_tool.utils.cancellation import TaskCancelled
from translation_tool.utils.cancellation import (
    raise_if_cancelled as _raise_op_cancelled,
)
from translation_tool.utils.log_unit import log_warning

PACK_MCMETA_NAME = "pack.mcmeta"
_RESOURCE_ROOTS = ("assets", "data")
_SCOPE_SAFE = re.compile(r"[^a-z0-9_.-]+")
_MAX_ISSUES = 200
# 這些掃描問題代表來源本身不可靠，LM 翻譯會跳過；段落數對不上的譯文檔（misaligned）只是不採用該檔
_BLOCKING_ISSUES = frozenset({"output_conflict", "read_failed", "source_missing"})


class MdPipelineError(RuntimeError):
    """來源或設定不安全、無法完成。"""


@dataclass(frozen=True)
class MdPipelineOptions:
    """一次執行的不可變設定快照。"""

    version: str
    dry_run: bool = True
    publish: bool = False
    output_root: str | None = None
    translate_missing: bool = False
    read_zh_cn: bool = True

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise MdPipelineError("遊戲版本不可為空")
        if self.publish and self.dry_run:
            raise MdPipelineError("正式發布不可使用預覽模式")
        if self.publish and not self.output_root:
            raise MdPipelineError("發布需要輸出路徑")


@dataclass
class MdSourceFile:
    """一個要輸出繁中的英文來源檔與它的段落。"""

    rel: str  # 相對於來源資料夾的 POSIX 路徑
    out_rel: str  # 輸出相對路徑（語言資料夾換成 zh_tw）
    mod_id: str
    file_key: str
    text: str
    blocks: list[Any]  # GuideBlock
    translatable: set[int] = field(default_factory=set)

    def item_key(self, index: int) -> str:
        return f"{self.file_key}#{index}"


@dataclass
class MdScanResult:
    """來源資料夾的掃描結果；不含任何資料庫寫入副作用。"""

    root: str = ""
    files: list[MdSourceFile] = field(default_factory=list)
    items: list[ScanItem] = field(default_factory=list)
    issues: list[dict[str, str]] = field(default_factory=list)
    files_seen: int = 0
    zh_cn_used_files: int = 0
    zh_tw_used_files: int = 0

    @property
    def mod_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.mod_id for item in self.items if item.mod_id}))

    def issue(self, rel: str, reason: str, detail: str = "") -> None:
        if len(self.issues) < _MAX_ISSUES:
            self.issues.append({"path": rel, "reason": reason, "detail": detail})


@dataclass
class MdOutputReport:
    """overlay 輸出結果。"""

    output_root: str = ""
    dry_run: bool = True
    files_planned: int = 0
    files_written: int = 0
    blocks_translated: int = 0
    blocks_missing: int = 0
    skipped_untranslated: int = 0
    skipped_manual_edit: int = 0
    issues: list[dict[str, str]] = field(default_factory=list)


@dataclass
class MdPipelineReport:
    """流水線的整體回報。"""

    options: MdPipelineOptions
    scan: MdScanResult
    ingest: IngestStats | None = None
    translation: dict[str, Any] = field(default_factory=dict)
    output: MdOutputReport | None = None
    timing_sec: dict[str, float] = field(default_factory=dict)

    @property
    def issues(self) -> list[dict[str, str]]:
        return [*self.scan.issues, *(self.output.issues if self.output else [])]

    @property
    def _translation_failed(self) -> bool:
        status = self.translation.get("status")
        return bool(status) and status not in ("DONE", "DONE_WITH_WARNINGS")

    def as_dict(self) -> dict[str, Any]:
        out = self.output
        return {
            "status": "DONE_WITH_WARNINGS"
            if self.issues or self._translation_failed
            else "DONE",
            "dry_run": self.options.dry_run,
            "publish": self.options.publish,
            "files_seen": self.scan.files_seen,
            "source_files": len(self.scan.files),
            "items_found": len(self.scan.items),
            "zh_cn_used_files": self.scan.zh_cn_used_files,
            "zh_tw_used_files": self.scan.zh_tw_used_files,
            "new_entries": self.ingest.new_entries if self.ingest else 0,
            "existing_entries": self.ingest.existing if self.ingest else 0,
            "translation": self.translation,
            "output_files": out.files_written if out else 0,
            "output_planned": out.files_planned if out else 0,
            "blocks_translated": out.blocks_translated if out else 0,
            "blocks_missing": out.blocks_missing if out else 0,
            "skipped_manual_edit": out.skipped_manual_edit if out else 0,
            "issues": self.issues[:_MAX_ISSUES],
            "timing_sec": dict(self.timing_sec),
        }


# ----------------------------------------------------------------- 來源探索


def discover_md_source_folders(path: str | Path) -> list[str]:
    """選到模組包根目錄時列出可掃描的資源資料夾；其他資料夾直接回傳自己。

    只看模組包慣例位置（``kubejs``、``resourcepacks/<包>``），不走訪整個模組包。回傳的是
    「含 ``assets`` 的那一層」，這樣掃描時相對路徑才是 ``assets/<模組>/…`` 的資源包結構。
    """
    root = Path(path).expanduser()
    if not root.is_dir():
        return []
    found: list[str] = []
    kubejs = root / "kubejs"
    if (kubejs / "assets").is_dir():
        found.append(str(kubejs.resolve()))
    packs = root / "resourcepacks"
    if packs.is_dir():
        found.extend(
            str(child.resolve())
            for child in sorted(packs.iterdir())
            if child.is_dir()
            and (child / "assets").is_dir()
            and not (child / MANIFEST_NAME).is_file()  # 本工具先前輸出的資源包不是來源
        )
    return found or [str(root.resolve())]


# ----------------------------------------------------------------- 掃描


def _scope_id(root: Path) -> str:
    """不在 ``assets|data/<模組>`` 之下的來源：用資料夾名稱組成穩定的 ID。"""
    name = _SCOPE_SAFE.sub("-", root.name.casefold()).strip("-")
    return f"md-{name or 'source'}"


def _identity(parts: list[str], root: Path) -> tuple[str, str]:
    """(mod_id, 中性檔案鍵)。"""
    if len(parts) > 2 and parts[0].casefold() in _RESOURCE_ROOTS:
        return parts[1], split_locale(parts[2:]).neutral
    return _scope_id(root), split_locale(parts).neutral


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def _lang_of(rel: str) -> str | None:
    return detect_lang_segment(rel.split("/"))


def scan_md_sources(
    source: str | Path,
    *,
    read_zh_cn: bool = True,
    convert: Callable[[str], str] | None = None,
    session=None,
) -> MdScanResult:
    """掃描來源資料夾內的指南 .md，組出 DB 條目。

    en_us（含無語言資料夾的指南）是原文；同名的 zh_tw、zh_cn 檔在段落數相同時
    逐段對應成既有譯文（zh_cn 需開啟 ``read_zh_cn``，並以 ``convert`` 轉繁）。
    """
    root = Path(source).expanduser().resolve()
    result = MdScanResult(root=str(root))
    if not root.is_dir():
        result.issue(str(root), "source_missing", "不是資料夾")
        return result

    by_key: dict[str, dict[str, list[str]]] = {}
    paths: dict[str, Path] = {}
    for path in iter_md_files(root):
        rel = path.relative_to(root).as_posix()
        result.files_seen += 1
        lang = _lang_of(rel)
        if lang not in ("en_us", "zh_tw", "zh_cn"):
            continue
        out_rel = map_rel_lang_path(
            map_rel_lang_path(rel, "en_us", "zh_tw"), "zh_cn", "zh_tw"
        )
        paths[rel] = path
        by_key.setdefault(out_rel.casefold(), {"en_us": [], "zh_tw": [], "zh_cn": []})[
            lang
        ].append(rel)
        if result.files_seen % 200 == 0:
            _raise_if_cancelled(session)

    for members in by_key.values():
        _scan_group(root, members, paths, read_zh_cn, convert, result)
    return result


def _scan_group(
    root: Path,
    members: dict[str, list[str]],
    paths: dict[str, Path],
    read_zh_cn: bool,
    convert: Callable[[str], str] | None,
    result: MdScanResult,
) -> None:
    en_files, tw_files, cn_files = (
        members["en_us"],
        members["zh_tw"],
        members["zh_cn"],
    )
    if len(en_files) > 1:
        for rel in en_files:
            result.issue(rel, "output_conflict", "多個來源會寫到同一個輸出")
        return
    if not en_files:
        return  # 只有譯文、沒有原文：沒有可翻譯的來源
    rel = en_files[0]
    text = _read_text(paths[rel])
    if text is None:
        result.issue(rel, "read_failed", "無法以 UTF-8 讀取")
        return
    parts = rel.split("/")
    mod_id, file_key = _identity(parts, root)
    blocks = extract_guide_blocks(text, parts[-1])
    out_rel = map_rel_lang_path(rel, "en_us", "zh_tw")
    source = MdSourceFile(rel, out_rel, mod_id, file_key, text, blocks)
    tw_blocks = _aligned(blocks, tw_files, paths, rel, result)
    cn_blocks = (
        _aligned(blocks, cn_files, paths, rel, result)
        if read_zh_cn and not tw_files
        else None
    )
    result.zh_tw_used_files += bool(tw_blocks)
    result.zh_cn_used_files += bool(cn_blocks)
    for index, block in enumerate(blocks):
        if contains_cjk(block.text) or not has_translatable_text(block.text):
            continue
        source.translatable.add(index)
        zh_tw = tw_blocks[index] if tw_blocks else ""
        zh_cn = cn_blocks[index] if cn_blocks else ""
        result.items.append(
            ScanItem(
                kind=KIND_GUIDE,
                mod_id=mod_id,
                key=source.item_key(index),
                en_us=block.text,
                zh_tw=zh_tw if contains_cjk(zh_tw) else "",
                zh_cn=zh_cn if contains_cjk(zh_cn) else "",
            )
        )
    result.files.append(source)


def _aligned(
    en_blocks: list[Any],
    rels: list[str],
    paths: dict[str, Path],
    en_rel: str,
    result: MdScanResult,
) -> list[str] | None:
    """譯文檔的段落與原文逐段對應；段落數不同代表譯文沒跟上，不匯入以免錯位。"""
    if len(rels) != 1:
        return None
    text = _read_text(paths[rels[0]])
    if text is None:
        return None
    other = [b.text for b in extract_guide_blocks(text, rels[0].rsplit("/", 1)[-1])]
    if len(other) != len(en_blocks):
        result.issue(
            rels[0],
            "misaligned",
            f"段落數 {len(other)} 與原文 {en_rel} 的 {len(en_blocks)} 不同",
        )
        return None
    return other


# ----------------------------------------------------------------- DB 同步與翻譯


def sync_database(
    db: TranslationDB,
    scan: MdScanResult,
    options: MdPipelineOptions,
    convert: Callable[[str], str] | None,
) -> IngestStats:
    """預覽只分類不寫入；正式模式把掃描結果寫入 DB（只新增、不覆蓋）。"""
    if options.dry_run:
        preview = db.scan_preview_target(options.version)
        return preview_ingest_many(preview, [scan.items], convert)[0]
    return db.ingest(options.version, scan.items, convert)


# ----------------------------------------------------------------- 輸出 overlay


def export_md_overlay(
    db: TranslationDB,
    scan: MdScanResult,
    options: MdPipelineOptions,
    *,
    session=None,
) -> MdOutputReport:
    """依 DB 的生效譯文輸出繁中指南；不修改來源，也不動 DB。"""
    from translation_tool.translation_db.quality import guide_structure_issues

    output = Path(str(options.output_root or "")).expanduser()
    # 只有發布才寫輸出資料夾；同步 DB 步驟即使填了輸出資料夾也只計算輸出計畫
    write_files = options.publish and not options.dry_run
    report = MdOutputReport(str(output), not write_files)
    source_root = Path(scan.root)
    if options.output_root and _inside(output.resolve(), source_root):
        raise MdPipelineError("輸出資料夾不可放在來源資料夾內")
    resolver = TranslationResolver(db, options.version, cross_version=False)
    guard = OutputGuard(output) if options.output_root else None
    wrote_resource_pack = False
    try:
        for count, source in enumerate(scan.files):
            if count % 50 == 0:
                _raise_if_cancelled(session)
            current = [b.text for b in source.blocks]
            translated = 0
            for index in sorted(source.translatable):
                block = source.blocks[index]
                hit = resolver.lookup(
                    KIND_GUIDE, source.mod_id, source.item_key(index), block.text
                )
                if (
                    hit
                    and hit.zh_tw.strip()
                    and not guide_structure_issues(
                        block.text, hit.zh_tw, kind=KIND_GUIDE
                    )
                ):
                    current[index] = hit.zh_tw
                    translated += 1
                else:
                    report.blocks_missing += 1
            report.blocks_translated += translated
            if translated == 0:
                report.skipped_untranslated += 1
                continue
            report.files_planned += 1
            if not write_files or guard is None:
                continue
            if _write_output(output, source, current, guard, report, source_root):
                wrote_resource_pack |= (
                    source.out_rel.split("/")[0].casefold() in _RESOURCE_ROOTS
                )
    finally:
        if guard is not None and write_files:
            guard.save()
    if wrote_resource_pack:
        _ensure_pack_mcmeta(output, options.version)
    return report


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _write_output(
    output: Path,
    source: MdSourceFile,
    blocks: list[str],
    guard: OutputGuard,
    report: MdOutputReport,
    source_root: Path,
) -> bool:
    target = output / source.out_rel
    resolved = target.resolve()
    # 輸出必須在輸出資料夾內，且不可落在來源資料夾內（輸出在來源上層時路徑可能剛好重疊）
    if not _inside(resolved, output.resolve()) or _inside(
        resolved, source_root.resolve()
    ):
        report.issues.append(
            {"path": source.rel, "reason": "unsafe_path", "detail": source.out_rel}
        )
        return False
    if guard.is_manually_edited(source.out_rel, target):
        report.skipped_manual_edit += 1
        report.issues.append(
            {
                "path": source.out_rel,
                "reason": "manual_edit",
                "detail": "輸出檔在上次寫入後被手動修改過，不覆蓋；要重新產生請先刪除該檔",
            }
        )
        return False
    doc = {
        "source": source.text,
        "ranges": [[b.start_line, b.end_line] for b in source.blocks],
        "prefixes": [b.prefix for b in source.blocks],
        "originals": [b.text for b in source.blocks],
        "blocks": blocks,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_guide(doc), encoding="utf-8")
    guard.record(source.out_rel, target)
    report.files_written += 1
    return True


def _ensure_pack_mcmeta(output: Path, version: str) -> None:
    """輸出資料夾還沒有 pack.mcmeta 時，依遊戲版本寫入對應的 pack_format。"""
    meta = output / PACK_MCMETA_NAME
    if meta.exists():
        return
    try:
        pack_format = resolve_version_input(version).pack_format
    except ValueError:
        pack_format = resolve_version_input("").pack_format
    meta.write_text(
        json.dumps(
            build_pack_mcmeta("Minecraft-translate 指南繁中翻譯", pack_format),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ----------------------------------------------------------------- 流水線


def run_md_pipeline(
    db: TranslationDB | None,
    source: str | Path,
    options: MdPipelineOptions,
    *,
    session=None,
    convert: Callable[[str], str] | None = None,
    translation_runner: Callable[..., Any] | None = None,
) -> MdPipelineReport:
    """來源分析 → DB 同步 → 可選 LM 翻譯 → 可選 overlay 輸出。"""
    started = time.perf_counter()
    if db is None and not options.dry_run:
        raise MdPipelineError("正式同步需要資料庫；預覽模式可不提供資料庫")
    _raise_if_cancelled(session)
    scan = scan_md_sources(
        source, read_zh_cn=options.read_zh_cn, convert=convert, session=session
    )
    report = MdPipelineReport(options, scan)
    report.timing_sec["scan"] = time.perf_counter() - started
    _raise_if_cancelled(session)
    _set_progress(session, 0.35)

    if db is not None:
        phase = time.perf_counter()
        report.ingest = sync_database(db, scan, options, convert)
        report.timing_sec["db_sync"] = time.perf_counter() - phase
    _raise_if_cancelled(session)
    _set_progress(session, 0.65)

    if options.translate_missing and db is not None:
        blocking = [i for i in scan.issues if i["reason"] in _BLOCKING_ISSUES]
        phase = time.perf_counter()
        if blocking:
            log_warning(
                f"[Markdown 流水線] 來源有 {len(blocking)} 項阻擋性問題，跳過 LM 翻譯"
            )
            report.translation = {
                "status": "SKIPPED",
                "reason": "來源有阻擋性問題（輸出衝突或讀取失敗）",
            }
        else:
            if translation_runner is None:
                raise MdPipelineError("LM 翻譯已啟用，但未提供共用翻譯服務入口")
            report.translation = _summary(
                translation_runner(db, scan, options, session)
            )
        report.timing_sec["translation"] = time.perf_counter() - phase
    _raise_if_cancelled(session)
    _set_progress(session, 0.95)

    if db is not None and options.output_root:
        phase = time.perf_counter()
        report.output = export_md_overlay(db, scan, options, session=session)
        report.timing_sec["export"] = time.perf_counter() - phase
    report.timing_sec["total"] = time.perf_counter() - started
    return report


def _summary(value: Any) -> dict[str, Any]:
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return dict(as_dict())
    return dict(value) if isinstance(value, dict) else {}


def _set_progress(session, value: float) -> None:
    setter = getattr(session, "set_progress", None)
    if callable(setter):
        setter(value)


def _raise_if_cancelled(session) -> None:
    """在昂貴階段之間檢查取消，避免取消後仍輸出檔案。"""
    _raise_op_cancelled()
    if session is not None and getattr(session, "cancel_requested", False):
        raise TaskCancelled()
