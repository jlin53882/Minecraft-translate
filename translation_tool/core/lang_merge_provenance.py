"""Persist per-key provenance for translations filled from Mod DB during merge."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import Any

import orjson

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


def write_translation_provenance(
    output_root: str | Path,
    relative_output_path: str,
    entries: dict[str, dict[str, Any]],
) -> Path | None:
    """Update the hidden output-root manifest for one generated language file.

    Each entry records a digest instead of duplicating translation text. An empty
    mapping removes old provenance for that output file, preventing stale tags
    after a later merge no longer fills the same keys from Mod DB.
    """
    root = Path(output_root)
    rel = PurePosixPath(relative_output_path.replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"不安全的譯文輸出相對路徑：{relative_output_path}")
    key = rel.as_posix()
    manifest_path = root / _MANIFEST_NAME
    with _WRITE_LOCK:
        if manifest_path.exists():
            manifest = orjson.loads(manifest_path.read_bytes())
            if not isinstance(manifest, dict) or not isinstance(
                manifest.get("outputs", {}), dict
            ):
                raise ValueError(f"譯文來源標記檔格式錯誤：{manifest_path}")
        else:
            manifest = {"schema_version": 1, "outputs": {}}
        outputs = manifest.setdefault("outputs", {})
        if entries:
            outputs[key] = {
                "entries": {
                    str(entry_key): {
                        **{
                            key: value
                            for key, value in details.items()
                            if key != "value"
                        },
                        "value_sha256": hashlib.sha256(
                            str(details.get("value", "")).encode("utf-8")
                        ).hexdigest(),
                    }
                    for entry_key, details in entries.items()
                }
            }
        else:
            outputs.pop(key, None)
        if not outputs:
            manifest_path.unlink(missing_ok=True)
            return None
        root.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(manifest_path, manifest)
    return manifest_path


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    data = orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
