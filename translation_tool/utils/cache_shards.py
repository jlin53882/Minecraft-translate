import logging
import os
import re
from pathlib import Path
from typing import Any

import orjson as json


def _write_json_atomic(path: Path, data: dict[str, Any]):
    """Atomically replace ``path`` with JSON content.

    The function intentionally has no meaningful return value today.
    Callers may still choose to transparently forward the result so a future
    success/failure return contract can be introduced without changing the
    wrapper shape.
    """
    tmp_path = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_bytes(json.dumps(data, option=json.OPT_INDENT_2))
    os.replace(tmp_path, path)


def _get_active_shard_path(
    *,
    type_dir: Path,
    cache_type: str,
    active_shard_file: str,
) -> Path:
    active_file = type_dir / active_shard_file

    if not active_file.exists():
        pat = re.compile(rf"^{re.escape(cache_type)}_(\d+)\.json$", re.IGNORECASE)
        existing_shards: list[int] = []
        for f in type_dir.glob(f"{cache_type}_*.json"):
            m = pat.match(f.name)
            if m:
                existing_shards.append(int(m.group(1)))

        latest_id = max(existing_shards) if existing_shards else 1
        active_file.write_text(f"{latest_id:05d}", encoding="utf-8")

    shard_id_str = active_file.read_text(encoding="utf-8").strip()
    if not shard_id_str:
        shard_id_str = "00001"
        active_file.write_text(shard_id_str, encoding="utf-8")

    return type_dir / f"{cache_type}_{shard_id_str}.json"


def _rotate_shard_if_needed(
    *,
    type_dir: Path,
    cache_type: str,
    data: dict[str, Any],
    rolling_shard_size: int,
    active_shard_file: str,
    logger: logging.Logger | None = None,
) -> bool:
    if len(data) < rolling_shard_size:
        return False

    active_file = type_dir / active_shard_file
    if not active_file.exists():
        _get_active_shard_path(
            type_dir=type_dir,
            cache_type=cache_type,
            active_shard_file=active_shard_file,
        )

    cur_id = int((active_file.read_text(encoding="utf-8") or "1").strip())
    new_id = f"{cur_id + 1:05d}"
    active_file.write_text(new_id, encoding="utf-8")

    if logger:
        logger.info(f"🔁 {cache_type} rolling shard rotate → {new_id}")

    return True


def _save_entries_to_active_shards(
    *,
    type_dir: Path,
    cache_type: str,
    entries: dict[str, Any],
    rolling_shard_size: int,
    active_shard_file: str,
    force_new_shard: bool = False,
    logger: logging.Logger | None = None,
):
    if not entries:
        return

    active_file = type_dir / active_shard_file
    # Ensure the `.active` pointer exists before any branch below reads it
    # directly. The returned path is intentionally ignored here.
    _get_active_shard_path(
        type_dir=type_dir,
        cache_type=cache_type,
        active_shard_file=active_shard_file,
    )

    if force_new_shard:
        cur = int((active_file.read_text(encoding="utf-8") or "1").strip())
        nxt = cur + 1
        active_file.write_text(f"{nxt:05d}", encoding="utf-8")
        if logger:
            logger.info(f"🔁 {cache_type} 手動切新分片 -> {nxt:05d}")

    pending_items = list(entries.items())
    while pending_items:
        save_path = _get_active_shard_path(
            type_dir=type_dir,
            cache_type=cache_type,
            active_shard_file=active_shard_file,
        )

        current_data: dict[str, Any] = {}
        if save_path.exists():
            try:
                old_data = json.loads(save_path.read_bytes())
                if isinstance(old_data, dict):
                    current_data = old_data
            except Exception as e:
                if logger:
                    logger.warning(f"⚠️ 讀取舊分片失敗，將以空白分片續寫: {e}")

        if _rotate_shard_if_needed(
            type_dir=type_dir,
            cache_type=cache_type,
            data=current_data,
            rolling_shard_size=rolling_shard_size,
            active_shard_file=active_shard_file,
            logger=logger,
        ):
            continue

        capacity = max(0, rolling_shard_size - len(current_data))
        chunk = pending_items[:capacity]

        for k, v in chunk:
            current_data[k] = v

        _write_json_atomic(save_path, current_data)
        if logger:
            logger.info(
                f"💾 {cache_type} saved: {save_path.name} (+{len(chunk)} / total={len(current_data)})"
            )

        pending_items = pending_items[capacity:]
        if pending_items:
            # Pre-rotate for the next loop iteration when the current shard is now
            # full; the boolean return value is intentionally ignored here.
            _ = _rotate_shard_if_needed(
                type_dir=type_dir,
                cache_type=cache_type,
                data=current_data,
                rolling_shard_size=rolling_shard_size,
                active_shard_file=active_shard_file,
                logger=logger,
            )
