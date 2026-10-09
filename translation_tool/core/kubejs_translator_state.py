"""KubeJS pipeline snapshots and ownership-aware output synchronization."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

STATE_VERSION = 1
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_HASH_CHUNK_SIZE = 1024 * 1024
_OUTPUT_LOCKS: dict[str, threading.RLock] = {}
_OUTPUT_LOCKS_LOCK = threading.Lock()


def output_lock(output_root: Path) -> threading.RLock:
    """Return a process-local lock for one resolved KubeJS output root."""
    key = os.path.normcase(str(output_root.resolve()))
    with _OUTPUT_LOCKS_LOCK:
        return _OUTPUT_LOCKS.setdefault(key, threading.RLock())


def pipeline_state_root(output_root: Path) -> Path:
    return output_root.resolve() / "kubejs" / ".pipeline"


def new_run_id() -> str:
    return uuid.uuid4().hex


def _manifest_snapshot_path(
    run_root: Path, manifest: dict[str, Any] | None, key: str, default: str
) -> Path:
    relative = manifest.get(key, default) if manifest else default
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"KubeJS 狀態檔的 {key} 無效")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise ValueError(f"KubeJS 狀態檔的 {key} 必須是相對路徑")
    root = run_root.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"KubeJS 狀態檔的 {key} 路徑越界：{relative}") from exc
    return resolved


def create_run_layout(
    run_root: Path, manifest: dict[str, Any] | None = None
) -> dict[str, Path]:
    run_root = run_root.resolve()
    paths = {
        "run": run_root,
        "sources": run_root / "sources",
        "raw": run_root / "raw" / "kubejs",
        "pending": run_root / "待翻譯" / "kubejs",
        "translated": _manifest_snapshot_path(
            run_root, manifest, "translated_snapshot", "LM翻譯後/kubejs"
        ),
        "final": _manifest_snapshot_path(
            run_root, manifest, "final_snapshot", "完成/kubejs"
        ),
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def source_identity(source_root: Path) -> tuple[str, str]:
    """Return a stable full identity and a short on-disk identifier."""
    normalized = os.path.normcase(str(source_root.resolve())).casefold()
    identity = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return identity, identity[:20]


def list_relevant_source_files(source_root: Path, output_root: Path) -> list[Path]:
    """List files that the KubeJS extractor may read, excluding pipeline output."""
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    files: list[Path] = []
    for path in source_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(output_root)
        except ValueError:
            pass
        else:
            continue

        rel = path.relative_to(source_root).as_posix().lower()
        if (
            path.suffix.lower() == ".js"
            and "client_scripts/" in rel
            or path.suffix.lower() == ".json"
            and "/lang/" in f"/{rel}"
        ):
            files.append(path)
    return sorted(files, key=lambda p: p.relative_to(source_root).as_posix().casefold())


def fingerprint_source(source_root: Path, output_root: Path) -> tuple[str, int, int]:
    """Hash relevant source paths and bytes; empty sources have a stable hash too."""
    source_root = source_root.resolve()
    digest = hashlib.sha256(b"kubejs-source-v1")
    files = list_relevant_source_files(source_root, output_root)
    total_bytes = 0
    for path in files:
        relative = path.relative_to(source_root).as_posix()
        digest.update(b"\x1f" + relative.encode("utf-8") + b"\x1e")
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_SIZE):
                total_bytes += len(chunk)
                digest.update(chunk)
        digest.update(b"\x1d")
    return digest.hexdigest(), len(files), total_bytes


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(4):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                # Windows scanners/editors can hold a just-written manifest for
                # a short interval. Retry briefly; never remove the old target.
                time.sleep(0.025 * (attempt + 1))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_bytes(
        path,
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
    )


def atomic_copy_file(source: Path, destination: Path) -> None:
    atomic_write_bytes(destination, source.read_bytes())


def write_current_manifest(state_root: Path, manifest: dict[str, Any]) -> None:
    manifest_path = state_root / "current.json"
    state_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(manifest_path, manifest)


def load_current_manifest(state_root: Path) -> dict[str, Any] | None:
    manifest_path = state_root / "current.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"KubeJS 狀態檔無法讀取，已保留原檔：{manifest_path}: {exc}"
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("version") != STATE_VERSION:
        raise ValueError(f"不支援的 KubeJS 狀態檔版本：{manifest_path}")
    mirrors = manifest.get("mirrors", {})
    if not isinstance(mirrors, dict):
        raise TypeError(f"KubeJS 狀態檔 mirrors 格式無效：{manifest_path}")
    for category, files in mirrors.items():
        if not isinstance(category, str) or not isinstance(files, dict):
            raise TypeError(f"KubeJS 狀態檔 mirror ownership 格式無效：{manifest_path}")
        _validate_owned_files(files, manifest_path)
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"KubeJS 狀態檔的 run_id 無效：{manifest_path}")
    run_root = state_root / "runs" / run_id
    if not run_root.is_dir():
        raise ValueError(f"KubeJS 狀態檔指向不存在的快照，原檔已保留：{run_root}")
    sources = manifest.get("sources", [])
    if not isinstance(sources, list) or any(
        not isinstance(source, dict) for source in sources
    ):
        raise ValueError(f"KubeJS 狀態檔的 sources 欄位無效：{manifest_path}")
    for source in sources:
        snapshot = source.get("snapshot")
        if not isinstance(snapshot, str):
            raise TypeError(f"KubeJS 狀態檔含無效 source snapshot：{manifest_path}")
        source_root = (run_root / snapshot).resolve()
        try:
            source_root.relative_to(run_root.resolve())
        except ValueError as exc:
            raise ValueError(f"KubeJS 狀態檔 source 路徑越界：{snapshot}") from exc
        if not source_root.is_dir():
            raise ValueError(f"KubeJS 狀態檔 source snapshot 不存在：{source_root}")
    for key, default in (
        ("translated_snapshot", "LM翻譯後/kubejs"),
        ("final_snapshot", "完成/kubejs"),
    ):
        snapshot = _manifest_snapshot_path(run_root, manifest, key, default)
        if key in manifest and not snapshot.is_dir():
            raise ValueError(f"KubeJS 狀態檔 {key} 不存在：{snapshot}")
    return manifest


def run_root_for(state_root: Path, manifest: dict[str, Any]) -> Path:
    return state_root / "runs" / manifest["run_id"]


def _validate_owned_files(files: dict[str, Any], path: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    for relative, digest in files.items():
        relative_path = Path(relative) if isinstance(relative, str) else None
        if (
            relative_path is None
            or relative_path.is_absolute()
            or ".." in relative_path.parts
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
        ):
            raise ValueError(
                f"KubeJS ownership manifest 含無效路徑或雜湊，已保留原檔：{path}"
            )
        output[relative] = digest.lower()
    return output


def load_owned_files(path: Path) -> dict[str, str]:
    """Read a small relative-path-to-content-hash ownership manifest."""
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"KubeJS ownership manifest 無法讀取，已保留原檔：{path}: {exc}"
        ) from exc
    files = value.get("files") if isinstance(value, dict) else None
    if not isinstance(files, dict):
        raise TypeError(f"KubeJS ownership manifest 格式無效，已保留原檔：{path}")
    return _validate_owned_files(files, path)


def sync_managed_tree(
    source_root: Path,
    destination_root: Path,
    previous_owned: dict[str, str],
    *,
    conflict_details: list[dict[str, str]] | None = None,
) -> tuple[dict[str, str], list[str], int]:
    """Mirror a generated tree without replacing or deleting unowned/edited files.

    Returns the hashes now safely owned at the destination, conflicts, and the
    number of previously owned files removed. Empty directories are retained.
    """
    source_root = source_root.resolve()
    destination_root = destination_root.resolve()
    new_files = {
        path.relative_to(source_root).as_posix(): path
        for path in sorted(source_root.rglob("*"))
        if path.is_file()
    }
    new_hashes = {relative: sha256_file(path) for relative, path in new_files.items()}
    safe_paths: dict[str, Path] = {}
    for relative in set(previous_owned) | set(new_files):
        target = (destination_root / Path(relative)).resolve()
        try:
            target.relative_to(destination_root)
        except ValueError:
            continue
        safe_paths[relative] = target

    conflicts: list[str] = []
    now_owned: dict[str, str] = {}

    def conflict(relative: str, kind: str) -> None:
        conflicts.append(relative)
        if conflict_details is not None:
            conflict_details.append({"path": relative, "kind": kind})

    for relative, source in new_files.items():
        target = safe_paths.get(relative)
        if target is None:
            conflict(relative, "unsafe_relative_path")
            continue
        expected_hash = new_hashes[relative]
        old_hash = previous_owned.get(relative)
        if target.exists():
            try:
                actual_hash = sha256_file(target)
            except OSError:
                conflict(relative, "unreadable_existing_file")
                if old_hash is not None:
                    now_owned[relative] = old_hash
                continue
            if actual_hash == expected_hash:
                now_owned[relative] = expected_hash
                continue
            if target.suffix.lower() == ".json":
                try:
                    semantically_equal = json.loads(
                        target.read_text(encoding="utf-8")
                    ) == json.loads(source.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    conflict(relative, "semantic_compare_failed")
                    if old_hash is not None:
                        now_owned[relative] = old_hash
                    continue
                except json.JSONDecodeError:
                    semantically_equal = False
                if semantically_equal:
                    try:
                        atomic_copy_file(source, target)
                    except OSError:
                        conflict(relative, "write_failed")
                        if old_hash is not None:
                            now_owned[relative] = old_hash
                        continue
                    now_owned[relative] = expected_hash
                    continue
            if old_hash is None or actual_hash != old_hash:
                conflict(
                    relative,
                    "unowned_file_collision"
                    if old_hash is None
                    else "modified_owned_file",
                )
                if old_hash is not None:
                    now_owned[relative] = old_hash
                continue
        try:
            atomic_copy_file(source, target)
        except OSError:
            conflict(relative, "write_failed")
            if old_hash is not None:
                now_owned[relative] = old_hash
            continue
        now_owned[relative] = expected_hash

    removed = 0
    for relative, old_hash in previous_owned.items():
        if relative in new_files:
            continue
        target = safe_paths.get(relative)
        if target is None or not target.exists():
            continue
        try:
            if sha256_file(target) == old_hash:
                target.unlink()
                removed += 1
            else:
                conflict(relative, "modified_stale_owned_file")
                now_owned[relative] = old_hash
        except OSError:
            conflict(relative, "stale_file_cleanup_failed")
            now_owned[relative] = old_hash

    # Legacy output may predate the ownership manifest. Preserve it, but surface
    # it because the public folder can no longer be described as a full mirror.
    if destination_root.is_dir():
        for path in sorted(destination_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(destination_root).as_posix()
            if relative not in new_files and relative not in previous_owned:
                conflict(relative, "legacy_unowned_stale_file")

    return now_owned, conflicts, removed


def copy_snapshot_tree(source_root: Path, destination_root: Path) -> None:
    if source_root.is_dir():
        shutil.copytree(source_root, destination_root, dirs_exist_ok=True)


def commit_staging_run(staging_root: Path, committed_root: Path) -> bool:
    """Commit a completed private run, copying if Windows temporarily blocks rename.

    The destination uses a unique run id and is not referenced by ``current.json``
    until this function completes. A copy fallback is therefore safe even if the
    process stops partway through: the previous current manifest remains valid.
    Returns True when the copy fallback was used.
    """
    committed_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(staging_root, committed_root)
        return False
    except PermissionError:
        try:
            shutil.copytree(staging_root, committed_root)
        except OSError:
            shutil.rmtree(committed_root, ignore_errors=True)
            raise
        shutil.rmtree(staging_root, ignore_errors=True)
        return True
