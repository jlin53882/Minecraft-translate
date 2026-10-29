"""把 ModDB 生效譯文安全輸出回 KubeJS／FTB Quests 來源 overlay。

這裡不是另一套翻譯引擎，也不會修改模組包。它只把資料庫中已經有明確
``entry_origin``、且來源 hash 仍相符的生效譯文，交給既有格式 writer 寫入
隔離輸出目錄。KubeJS 輸出是 ``kubejs/`` overlay；FTB Quests 輸出是
``config/ftbquests/quests/`` overlay，兩者不會被誤塞進 Resource Pack。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from translation_tool.plugins.ftbquests.ftbquests_snbt_inject import (
    patch_lang_snbt_file,
    patch_quest_snbt_file,
)
from translation_tool.plugins.kubejs.kubejs_tooltip_inject import inject
from translation_tool.translation_db.models import EffectivePackTranslation
from translation_tool.translation_db.pack_sources import pack_scope_for_root
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.schema import (
    KIND_FTB_QUEST_LANG,
    KIND_FTB_QUESTS,
    KIND_KUBEJS_SCRIPT,
    KIND_LANG,
)

_LOCALES = {"en_us", "zh_cn", "zh_tw"}
_QUEST_FIELD = re.compile(
    r"(?:^|\.)(title|subtitle|description(?:\[\d+\])?)$"
)
_QUEST_ID = re.compile(r"\|id:(.+)$")


class PackOutputError(RuntimeError):
    """來源過期、格式衝突或輸出契約不成立時拒絕產出。"""


@dataclass
class PackOutputReport:
    """輸出預覽／結果；不把不支援內容冒充成已套用。"""

    pack_scope: str
    output_root: str
    dry_run: bool
    entries_considered: int = 0
    entries_applied: int = 0
    files_written: int = 0
    source_files_checked: int = 0
    unsupported: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    stale_sources: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unsupported and not self.conflicts and not self.stale_sources

    def as_dict(self) -> dict[str, Any]:
        return {
            "pack_scope": self.pack_scope,
            "output_root": self.output_root,
            "dry_run": self.dry_run,
            "entries_considered": self.entries_considered,
            "entries_applied": self.entries_applied,
            "files_written": self.files_written,
            "source_files_checked": self.source_files_checked,
            "unsupported": list(self.unsupported),
            "conflicts": list(self.conflicts),
            "stale_sources": list(self.stale_sources),
            "ok": self.ok,
        }


@dataclass(frozen=True)
class _PlannedTranslation:
    item: EffectivePackTranslation
    target: str
    value: str


def export_pack_overlay(
    db: TranslationDB,
    pack_root: str | Path,
    version: str,
    output_root: str | Path,
    *,
    pack_scope: str | None = None,
    dry_run: bool = False,
) -> PackOutputReport:
    """輸出生效譯文 overlay，不修改來源模組包或資料庫。

    ``output_root`` 是專用輸出目錄，成功後會包含零或多個：

    * ``kubejs/``：腳本 patch 與 KubeJS assets lang。
    * ``config/ftbquests/quests/``：FTB Quests SNBT overlay。

    Resource Pack 打包器不會自動收集這些目錄；呼叫端必須依部署類型另行
    包裝，避免把 Config／腳本內容偽裝成 Resource Pack。
    """
    root = Path(pack_root).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
    if not root.is_dir():
        raise PackOutputError(f"模組包根目錄不存在：{root}")
    if output == root or root in output.parents:
        raise PackOutputError("輸出目錄不可位於來源模組包內")
    scope = pack_scope or pack_scope_for_root(root)
    report = PackOutputReport(str(scope), str(output), bool(dry_run))
    items = db.effective_pack_translations(version, scope)
    report.entries_considered = len(items)
    plans = _make_plan(root, items, report)
    _validate_sources(root, plans, report)
    if not report.ok:
        raise PackOutputError(_format_report_error(report))
    if dry_run:
        report.entries_applied = len(plans)
        return report

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    try:
        report.entries_applied = _write_plan(root, stage, plans)
        report.files_written = sum(1 for path in stage.rglob("*") if path.is_file())
        _publish_stage(stage, output)
        return report
    except PackOutputError:
        raise
    except (OSError, UnicodeError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        raise PackOutputError(f"輸出 overlay 失敗，未修改來源：{exc}") from exc
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _make_plan(
    pack_root: Path,
    items: list[EffectivePackTranslation],
    report: PackOutputReport,
) -> list[_PlannedTranslation]:
    plans: list[_PlannedTranslation] = []
    seen: dict[tuple[str, str], str] = {}
    for item in items:
        if not item.zh_tw.strip() or item.zh_tw == item.en_us:
            continue
        target = _target_for(item)
        if target is None:
            report.unsupported.append(
                f"entry={item.entry_id} kind={item.kind} source={item.origin.source_path}"
            )
            continue
        key = (target.casefold(), _target_selector(item))
        previous = seen.get(key)
        if previous is not None and previous != item.zh_tw:
            report.conflicts.append(
                f"目標碰撞：{target} [{_target_selector(item)}]"
            )
            continue
        seen[key] = item.zh_tw
        plans.append(_PlannedTranslation(item, target, item.zh_tw))
    return plans


def _target_for(item: EffectivePackTranslation) -> str | None:
    origin = item.origin
    if origin.source_kind == KIND_KUBEJS_SCRIPT:
        return f"kubejs-script/{origin.source_path}"
    if origin.source_kind == KIND_LANG and origin.format_profile == "kubejs-js":
        return f"kubejs-script/{origin.source_path}"
    if origin.source_kind == KIND_LANG and origin.format_profile == "lang-json":
        return _kubejs_lang_target(origin.source_path)
    if origin.source_kind == KIND_FTB_QUESTS:
        return f"ftb-quest/{origin.source_path}"
    if origin.source_kind == KIND_FTB_QUEST_LANG:
        return f"ftb-lang/{origin.source_path}"
    return None


def _target_selector(item: EffectivePackTranslation) -> str:
    origin = item.origin
    if origin.source_kind == KIND_LANG and origin.format_profile == "lang-json":
        return _lang_key(item.key)
    if origin.source_kind == KIND_LANG and origin.format_profile == "kubejs-js":
        return f"{origin.source_path}|lang:{item.key}"
    return item.key


def _validate_sources(
    pack_root: Path,
    plans: list[_PlannedTranslation],
    report: PackOutputReport,
) -> None:
    checked: set[Path] = set()
    for plan in plans:
        source = _source_path(pack_root, plan.item)
        if source in checked:
            continue
        checked.add(source)
        report.source_files_checked += 1
        if not source.is_file():
            report.stale_sources.append(str(source))
            continue
        expected = plan.item.origin.source_hash
        if expected and _sha256(source) != expected:
            report.stale_sources.append(str(source))


def _write_plan(pack_root: Path, stage: Path, plans: list[_PlannedTranslation]) -> int:
    kubejs_maps: dict[str, dict[str, str]] = defaultdict(dict)
    lang_updates: dict[str, dict[str, str]] = defaultdict(dict)
    quest_updates: dict[str, dict[str, str]] = defaultdict(dict)
    ftb_lang_updates: dict[str, dict[str, str]] = defaultdict(dict)

    for plan in plans:
        item = plan.item
        origin = item.origin
        if origin.source_kind == KIND_KUBEJS_SCRIPT or (
            origin.source_kind == KIND_LANG and origin.format_profile == "kubejs-js"
        ):
            key = _target_selector(item)
            kubejs_maps[origin.source_path][key] = plan.value
        elif origin.source_kind == KIND_LANG:
            lang_updates[plan.target][_lang_key(item.key)] = plan.value
        elif origin.source_kind == KIND_FTB_QUESTS:
            update_key = _quest_update_key(item)
            if update_key is not None:
                quest_updates[origin.source_path][update_key] = plan.value
        elif origin.source_kind == KIND_FTB_QUEST_LANG:
            update_key = _ftb_lang_update_key(origin.selector)
            if update_key is not None:
                ftb_lang_updates[origin.source_path][update_key] = plan.value

    applied = 0
    if kubejs_maps:
        with tempfile.TemporaryDirectory(prefix="pack-output-kubejs-") as temp:
            translated = Path(temp)
            for source_path, values in kubejs_maps.items():
                target = translated / Path(source_path).with_suffix(".json")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
            result = inject(
                str(pack_root / "kubejs"),
                str(translated),
                str(stage / "kubejs"),
            )
            applied += sum(1 for _ in kubejs_maps.values())
            del result

    for relative_target, values in lang_updates.items():
        destination = stage / "kubejs" / relative_target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        applied += len(values)

    quests_root = pack_root / "config" / "ftbquests" / "quests"
    for source_path, updates in quest_updates.items():
        src = _safe_join(quests_root, source_path)
        dst = stage / "config" / "ftbquests" / "quests" / _safe_relative(source_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        changed, _candidates = patch_quest_snbt_file(str(src), str(dst), updates)
        applied += changed

    for source_path, updates in ftb_lang_updates.items():
        src = _safe_join(quests_root, source_path)
        target_path = _ftb_lang_target(source_path)
        dst = stage / "config" / "ftbquests" / "quests" / target_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copy2(src, dst)
        changed, _candidates = patch_lang_snbt_file(str(src), str(dst), updates)
        applied += changed
    return applied


def _source_path(pack_root: Path, item: EffectivePackTranslation) -> Path:
    origin = item.origin
    if origin.source_kind == KIND_FTB_QUESTS or origin.source_kind == KIND_FTB_QUEST_LANG:
        return _safe_join(pack_root / "config" / "ftbquests" / "quests", origin.source_path)
    return _safe_join(pack_root / "kubejs", origin.source_path)


def _safe_relative(value: str) -> str:
    raw = value.replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not raw
        or raw.endswith("/")
        or "\x00" in raw
        or (path.parts and ":" in path.parts[0])
    ):
        raise PackOutputError(f"非法來源相對路徑：{value}")
    return path.as_posix()


def _safe_join(root: Path, value: str) -> Path:
    relative = Path(_safe_relative(value))
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise PackOutputError(f"來源路徑越界：{value}") from exc
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _kubejs_lang_target(source_path: str) -> str:
    relative = _safe_relative(source_path)
    parts = list(PurePosixPath(relative).parts)
    if len(parts) < 3 or parts[-2].lower() != "lang":
        raise PackOutputError(f"KubeJS lang 來源路徑不符合 assets/<namespace>/lang：{source_path}")
    parts[-1] = "zh_tw.json"
    return "/".join(parts)


def _lang_key(key: str) -> str:
    return key


def _quest_update_key(item: EffectivePackTranslation) -> str | None:
    id_match = _QUEST_ID.search(item.key)
    field_match = _QUEST_FIELD.search(item.origin.selector)
    if id_match is None or field_match is None:
        return None
    return f"id:{id_match.group(1)}|{field_match.group(1)}"


def _ftb_lang_update_key(selector: str) -> str | None:
    if not selector.startswith("lang:"):
        return None
    return selector[5:]


def _ftb_lang_target(source_path: str) -> str:
    relative = _safe_relative(source_path)
    parts = list(PurePosixPath(relative).parts)
    if not parts or parts[0].lower() != "lang":
        return relative
    if len(parts) >= 2 and parts[1].lower() in _LOCALES:
        parts[1] = "zh_tw"
    elif len(parts) == 2:
        stem = Path(parts[1]).stem.lower()
        if stem in _LOCALES:
            parts[1] = "zh_tw" + Path(parts[1]).suffix
    return "/".join(parts)


def _publish_stage(stage: Path, output: Path) -> None:
    """以完整快照替換輸出目錄，避免保留上一次輸出的幽靈檔案。"""
    if output.exists() and not output.is_dir():
        raise PackOutputError(f"輸出路徑不是資料夾：{output}")
    backup: Path | None = None
    if output.exists():
        backup = output.with_name(f".{output.name}.backup-{uuid.uuid4().hex}")
        output.replace(backup)
    try:
        stage.replace(output)
    except OSError:
        if backup is not None and not output.exists():
            backup.replace(output)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)


def _format_report_error(report: PackOutputReport) -> str:
    parts = ["沒有產生 overlay，請先處理來源驗證問題"]
    if report.stale_sources:
        parts.append("stale=" + ", ".join(report.stale_sources[:5]))
    if report.conflicts:
        parts.append("conflict=" + ", ".join(report.conflicts[:5]))
    if report.unsupported:
        parts.append("unsupported=" + ", ".join(report.unsupported[:5]))
    return "；".join(parts)


__all__ = ["PackOutputError", "PackOutputReport", "export_pack_overlay"]
