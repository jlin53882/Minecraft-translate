"""智慧校對歷史目錄的維護工具：檢查、修復、備份、還原與清理。

用法（從 repository root 執行；``--db`` 指向翻譯資料庫，``--dir`` 直接指定歷史目錄）：

    python tools/smart_review_history_tool.py --db data/mod_db.sqlite check
    python tools/smart_review_history_tool.py --db data/mod_db.sqlite repair --dry-run
    python tools/smart_review_history_tool.py --db data/mod_db.sqlite repair
    python tools/smart_review_history_tool.py --db data/mod_db.sqlite backup --output history.zip
    python tools/smart_review_history_tool.py --db data/mod_db.sqlite restore --input history.zip --force
    python tools/smart_review_history_tool.py --db data/mod_db.sqlite prune --retention-days 90 --keep-latest 10
    python tools/smart_review_history_tool.py --db data/mod_db.sqlite prune --apply

``prune`` 預設只列出會刪除的批次，加 ``--apply`` 才真的刪除。``restore`` 與 ``prune --apply``
會改動歷史目錄，請先關閉應用程式。格式與流程見
``docs/developer-guide/smart-review-history-storage.md``。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation_tool.translation_db.smart_review_history import (  # noqa: E402
    history_directory_for_db,
)
from translation_tool.translation_db.smart_review_history_lock import (  # noqa: E402
    HistoryLock,
)
from translation_tool.translation_db.smart_review_history_migrations import (  # noqa: E402
    check_history_directory,
    repair_history_directory,
    summarize_issues,
)

DEFAULT_RETENTION_DAYS = 90
DEFAULT_KEEP_LATEST = 10
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOCK_FILE_NAME = ".smart_review.lock"
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_FINISHED_RUN_STATUSES = frozenset(
    {"completed", "partial", "cancelled", "failed", "interrupted"}
)


class ToolError(Exception):
    """可預期的使用錯誤；訊息直接顯示給使用者。"""


# ---------------------------------------------------------------------------
# 備份與還原
# ---------------------------------------------------------------------------


def _is_backup_member(relative: Path) -> bool:
    """備份排除鎖檔與寫入中的暫存檔；其餘（含 quarantine）全部保留。"""
    return relative.name != _LOCK_FILE_NAME and not any(
        part.startswith(".") and part.endswith(".tmp") for part in relative.parts
    )


def backup_history(directory: Path, output: Path) -> int:
    """把整個歷史目錄打包成 zip，回傳檔案數。持有寫入鎖以取得一致的快照。"""
    if not directory.is_dir():
        raise ToolError(f"歷史目錄不存在：{directory}")
    resolved_output = output.resolve()
    if directory.resolve() in resolved_output.parents:
        raise ToolError("備份檔不可放在歷史目錄內")
    if output.exists():
        raise ToolError(f"備份檔已存在，請換一個路徑：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    count = 0
    try:
        with (
            HistoryLock(directory).write(),
            zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive,
        ):
            for path in sorted(directory.rglob("*")):
                relative = path.relative_to(directory)
                if path.is_file() and _is_backup_member(relative):
                    archive.write(path, relative.as_posix())
                    count += 1
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return count


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """拒絕絕對路徑與 ``..`` 的 zip 成員，避免還原時寫到歷史目錄之外。"""
    members = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        parts = Path(info.filename).parts
        if Path(info.filename).is_absolute() or ".." in parts or not parts:
            raise ToolError(f"備份檔含有不安全的路徑：{info.filename}")
        members.append(info)
    return members


def restore_history(
    directory: Path, source: Path, *, force: bool
) -> tuple[int, Path | None]:
    """用備份 zip 取代歷史目錄；回傳 (檔案數, 被換下的舊目錄)。

    先解壓到同層暫存目錄，成功後才把現有目錄改名為 ``<目錄>.pre-restore-<時間>``（不刪除），
    再把暫存目錄改成歷史目錄。現有目錄非空時必須指定 ``force``。
    """
    if not source.is_file():
        raise ToolError(f"找不到備份檔：{source}")
    existing = directory.is_dir() and any(directory.iterdir())
    if existing and not force:
        raise ToolError("歷史目錄已有資料；確認要取代請加 --force（舊目錄會保留）")
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = directory.with_name(f".{directory.name}.restore.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(source) as archive:
            members = _safe_members(archive)
            for info in members:
                target = staging / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as reader, target.open("wb") as writer:
                    shutil.copyfileobj(reader, writer)
        staging.mkdir(parents=True, exist_ok=True)
        previous: Path | None = None
        if directory.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            previous = directory.with_name(f"{directory.name}.pre-restore-{stamp}")
            directory.rename(previous)
        staging.rename(directory)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    # mtime 不會隨 zip 還原，manifest 的分片版本記錄需要重建；內容不變。
    repair_history_directory(directory)
    return len(members), previous


# ---------------------------------------------------------------------------
# retention 清理
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PruneCandidate:
    """一個依 retention 規則會被刪除的批次。"""

    run_id: str
    started_at: str
    status: str
    files: int


def _parse_started_at(value: object) -> datetime | None:
    """解析批次的 UTC 開始時間；格式無法辨識時回傳 None（視為不可刪）。"""
    try:
        return datetime.strptime(str(value), _TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _run_files(directory: Path, run_id: str) -> list[Path]:
    """批次的全部檔案：manifest 與分片目錄內的檔案。"""
    files = [directory / f"{run_id}.json"]
    chunk_directory = directory / f"{run_id}.chunks"
    if chunk_directory.is_dir():
        files.extend(
            path for path in sorted(chunk_directory.rglob("*")) if path.is_file()
        )
    return files


def _manifest_issue_run_ids(directory: Path) -> set[str]:
    """回傳檢查器判定有問題的批次；有任何問題就不進行破壞性清理。"""
    return {
        str(issue.run_id)
        for issue in check_history_directory(directory)
        if issue.run_id
    }


def _is_prunable_manifest(
    payload: object, *, run_id: str, started_at: str, status: str
) -> bool:
    """確認 manifest 仍是規劃時看到的完整、已結束批次。"""
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("id") == run_id
        and payload.get("started_at") == started_at
        and payload.get("status") == status
        and status in _FINISHED_RUN_STATUSES
    )


def plan_prune(
    directory: Path,
    *,
    retention_days: int,
    keep_latest: int,
    now: datetime | None = None,
) -> list[PruneCandidate]:
    """列出可刪除的批次：早於 retention 天數，且不在最近 ``keep_latest`` 批之內。

    ``running`` 狀態、開始時間無法辨識或 manifest 損壞的批次一律保留，交給 ``check``／
    ``repair`` 處理。排序以 ``started_at`` 由新到舊。
    """
    if retention_days < 0 or keep_latest < 0:
        raise ToolError("--retention-days 與 --keep-latest 不可為負數")
    if not directory.is_dir():
        return []
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    invalid_run_ids = _manifest_issue_run_ids(directory)
    runs: list[tuple[str, str, dict[str, Any]]] = []
    for path in directory.glob("*.json"):
        if not _RUN_ID.fullmatch(path.stem) or path.stem in invalid_run_ids:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("id") == path.stem:
            status = str(payload.get("status") or "")
            if status in _FINISHED_RUN_STATUSES:
                runs.append((str(payload.get("started_at") or ""), path.stem, payload))
    runs.sort(reverse=True)
    candidates = []
    for started_at, run_id, payload in runs[keep_latest:]:
        started = _parse_started_at(started_at)
        if started is None or started >= cutoff or payload.get("status") == "running":
            continue
        candidates.append(
            PruneCandidate(
                run_id,
                started_at,
                str(payload.get("status") or ""),
                len(_run_files(directory, run_id)),
            )
        )
    return candidates


def apply_prune(directory: Path, candidates: list[PruneCandidate]) -> int:
    """刪除清單中的批次（manifest 最後刪除前先移除分片，避免留下孤立的分片目錄）。"""
    removed = 0
    with HistoryLock(directory).write():
        invalid_run_ids = _manifest_issue_run_ids(directory)
        for candidate in candidates:
            if candidate.run_id in invalid_run_ids:
                continue
            manifest = directory / f"{candidate.run_id}.json"
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not _is_prunable_manifest(
                payload,
                run_id=candidate.run_id,
                started_at=candidate.started_at,
                status=candidate.status,
            ):
                continue
            shutil.rmtree(directory / f"{candidate.run_id}.chunks", ignore_errors=True)
            manifest.unlink(missing_ok=True)
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_directory(args: argparse.Namespace) -> Path:
    """由 ``--dir`` 或 ``--db`` 決定歷史目錄。"""
    if args.dir:
        return Path(args.dir).expanduser()
    return history_directory_for_db(args.db)


def _emit(
    args: argparse.Namespace, payload: dict[str, Any], text_lines: list[str]
) -> None:
    """依 ``--json`` 輸出 JSON 或人類可讀文字。"""
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("\n".join(text_lines))


def _cmd_check(args: argparse.Namespace) -> int:
    directory = _resolve_directory(args)
    issues = check_history_directory(directory)
    lines = [f"歷史目錄：{directory}"]
    lines += [f"[{i.severity}] {i.code} {i.path}：{i.detail}" for i in issues]
    lines.append("沒有發現問題" if not issues else f"共 {len(issues)} 個問題")
    _emit(
        args,
        {
            "directory": str(directory),
            "ok": not issues,
            "summary": summarize_issues(issues),
            "issues": [issue.to_dict() for issue in issues],
        },
        lines,
    )
    return 1 if issues else 0


def _cmd_repair(args: argparse.Namespace) -> int:
    directory = _resolve_directory(args)
    report = repair_history_directory(directory, dry_run=args.dry_run)
    verb = "預計" if report.dry_run else "已"
    lines = [f"歷史目錄：{directory}"]
    lines += [f"{verb} {a.kind} {a.path}：{a.detail}" for a in report.actions]
    lines += [f"[未解決] {i.code} {i.path}：{i.detail}" for i in report.unresolved]
    if report.quarantine_directory:
        lines.append(f"孤立檔案已搬到：{report.quarantine_directory}")
    lines.append(
        f"{'dry-run：未改任何檔案；' if report.dry_run else ''}動作 {len(report.actions)} 個，未解決 {len(report.unresolved)} 個"
    )
    _emit(
        args,
        {
            "directory": str(directory),
            "dry_run": report.dry_run,
            "actions": [a.to_dict() for a in report.actions],
            "unresolved": [i.to_dict() for i in report.unresolved],
            "quarantine_directory": report.quarantine_directory,
        },
        lines,
    )
    return 1 if report.unresolved else 0


def _cmd_backup(args: argparse.Namespace) -> int:
    directory = _resolve_directory(args)
    count = backup_history(directory, Path(args.output))
    _emit(
        args,
        {"directory": str(directory), "output": args.output, "files": count},
        [f"已備份 {count} 個檔案到 {args.output}"],
    )
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    directory = _resolve_directory(args)
    count, previous = restore_history(directory, Path(args.input), force=args.force)
    lines = [f"已還原 {count} 個檔案到 {directory}"]
    if previous is not None:
        lines.append(f"原本的目錄保留在：{previous}")
    _emit(
        args,
        {
            "directory": str(directory),
            "files": count,
            "previous_directory": None if previous is None else str(previous),
        },
        lines,
    )
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    directory = _resolve_directory(args)
    candidates = plan_prune(
        directory, retention_days=args.retention_days, keep_latest=args.keep_latest
    )
    removed = apply_prune(directory, candidates) if args.apply else 0
    lines = [f"歷史目錄：{directory}"]
    lines += [
        f"{'已刪除' if args.apply else '將刪除'} {c.run_id}（開始 {c.started_at}，{c.status}，{c.files} 個檔案）"
        for c in candidates
    ]
    lines.append(
        f"已刪除 {removed} 個批次"
        if args.apply
        else f"dry-run：符合條件 {len(candidates)} 個批次，加 --apply 才會刪除"
    )
    _emit(
        args,
        {
            "directory": str(directory),
            "apply": bool(args.apply),
            "retention_days": args.retention_days,
            "keep_latest": args.keep_latest,
            "candidates": [c.__dict__ for c in candidates],
            "removed": removed,
        },
        lines,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """建立命令列解析器。"""
    parser = argparse.ArgumentParser(description="智慧校對歷史目錄維護工具")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--db", help="翻譯資料庫路徑（歷史目錄為 <名稱>.smart_review）")
    target.add_argument("--dir", help="直接指定歷史目錄")
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "check", help="檢查 manifest、分片與 sidecar 是否一致"
    ).set_defaults(func=_cmd_check)
    repair = commands.add_parser(
        "repair", help="重建摘要與 sidecar，孤立檔案搬到 quarantine"
    )
    repair.add_argument("--dry-run", action="store_true", help="只列出動作，不改檔案")
    repair.set_defaults(func=_cmd_repair)
    backup = commands.add_parser("backup", help="把整個歷史目錄打包成 zip")
    backup.add_argument("--output", required=True, help="zip 檔路徑（不可已存在）")
    backup.set_defaults(func=_cmd_backup)
    restore = commands.add_parser("restore", help="用備份 zip 取代歷史目錄")
    restore.add_argument("--input", required=True, help="備份 zip 路徑")
    restore.add_argument("--force", action="store_true", help="允許取代已有資料的目錄")
    restore.set_defaults(func=_cmd_restore)
    prune = commands.add_parser("prune", help="依 retention 清理舊批次（預設 dry-run）")
    prune.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    prune.add_argument("--keep-latest", type=int, default=DEFAULT_KEEP_LATEST)
    prune.add_argument(
        "--apply", action="store_true", help="真的刪除（未指定時只列出）"
    )
    prune.set_defaults(func=_cmd_prune)
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令列進入點；回傳程序結束碼（0 成功、1 有問題、2 使用錯誤）。"""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ToolError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
