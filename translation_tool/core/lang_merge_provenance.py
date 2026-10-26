"""Persist per-key provenance for translations filled from Mod DB during merge."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

import orjson

from translation_tool.utils.file_replace import replace_with_retry
from translation_tool.utils.log_unit import log_warning

_MANIFEST_NAME = ".translation-provenance.json"
_WRITE_LOCK = threading.RLock()


def source_provenance(hit, catalog) -> dict[str, Any]:
    """Describe the database source used for one merge fill."""
    from translation_tool.translation_db.schema import SRC_AI, SRC_AI_REPAIR

    label = (
        "AI補譯修正"
        if hit.source in {SRC_AI, SRC_AI_REPAIR}
        else catalog.label(hit.source)
    )
    return {
        "label": label,
        "source": catalog.token_for(hit.source),
        "database_version": hit.mc_version,
        "cross_version": hit.cross,
    }


class _Batch:
    """一次合併的來源標記暫存：``root`` 底下的寫入先累積在記憶體，離開時每個檔案只寫一次。"""

    def __init__(self, root: str) -> None:
        self.root = root
        self.manifests: dict[Path, dict[str, Any]] = {}


_BATCHES: list[_Batch] = []  # 受 _WRITE_LOCK 保護


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


@contextmanager
def provenance_batch(output_root: str | Path) -> Iterator[None]:
    """合併期間延後寫入來源標記檔：同一個輸出根目錄的標記只在離開時寫一次。

    不使用時每個語言檔都要讀出、重寫並 fsync 整份標記檔，輸出模組越多越慢（O(n²)），
    而且所有工作執行緒都排在同一把鎖後面。離開時（含取消、例外）一定寫出；
    寫出失敗只記錄警告（譯文檔已寫入，來源標記只影響畫面標籤）。
    """
    batch = _Batch(os.path.abspath(output_root))
    with _WRITE_LOCK:
        _BATCHES.append(batch)
    try:
        yield
    finally:
        with _WRITE_LOCK:
            _BATCHES.remove(batch)
            for manifest_path, manifest in batch.manifests.items():
                try:
                    _persist_manifest(manifest_path, manifest)
                except OSError as exc:
                    log_warning(f"來源標記檔更新失敗（已重試）：{manifest_path}: {exc}")


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        return {"schema_version": 1, "outputs": {}}
    manifest = orjson.loads(manifest_path.read_bytes())
    if not isinstance(manifest, dict) or not isinstance(
        manifest.get("outputs", {}), dict
    ):
        raise ValueError(f"譯文來源標記檔格式錯誤：{manifest_path}")  # noqa: TRY004
    return manifest


def _apply_entries(
    manifest: dict[str, Any], key: str, entries: dict[str, dict[str, Any]]
) -> None:
    outputs = manifest.setdefault("outputs", {})
    if not entries:
        outputs.pop(key, None)
        return
    outputs[key] = {
        "entries": {
            str(entry_key): {
                **{k: v for k, v in details.items() if k != "value"},
                "value_sha256": hashlib.sha256(
                    str(details.get("value", "")).encode("utf-8")
                ).hexdigest(),
            }
            for entry_key, details in entries.items()
        }
    }


def _persist_manifest(manifest_path: Path, manifest: dict[str, Any]) -> Path | None:
    if not manifest.get("outputs"):
        manifest_path.unlink(missing_ok=True)
        return None
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(manifest_path, manifest)
    return manifest_path


def write_translation_provenance(
    output_root: str | Path,
    relative_output_path: str,
    entries: dict[str, dict[str, Any]],
) -> Path | None:
    """Update the hidden output-root manifest for one generated language file.

    Each entry records a digest instead of duplicating translation text. An empty
    mapping removes old provenance for that output file, preventing stale tags
    after a later merge no longer fills the same keys from Mod DB. Inside a
    ``provenance_batch`` the update stays in memory until the batch exits.
    """
    root = Path(output_root)
    rel = PurePosixPath(relative_output_path.replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"不安全的譯文輸出相對路徑：{relative_output_path}")
    key = rel.as_posix()
    manifest_path = root / _MANIFEST_NAME
    with _WRITE_LOCK:
        root_abs = os.path.abspath(root)
        batch = next((b for b in _BATCHES if _is_under(root_abs, b.root)), None)
        if batch is not None:
            manifest = batch.manifests.get(manifest_path)
            if manifest is None:
                manifest = batch.manifests[manifest_path] = _load_manifest(
                    manifest_path
                )
            _apply_entries(manifest, key, entries)
            return manifest_path if manifest.get("outputs") else None
        manifest = _load_manifest(manifest_path)
        _apply_entries(manifest, key, entries)
        return _persist_manifest(manifest_path, manifest)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    data = orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
