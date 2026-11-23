"""KubeJS pipeline snapshots and ownership-aware output synchronization."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from translation_tool.utils.file_replace import replace_with_retry

_HASH_CHUNK_SIZE = 1024 * 1024


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
        # Windows scanners/editors can hold a just-written manifest for a short
        # interval; replace_with_retry backs off and never removes the old target.
        replace_with_retry(temporary, path)
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


