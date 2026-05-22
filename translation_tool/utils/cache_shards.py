"""translation_tool/utils/cache_shards.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import logging
import os
import re
from pathlib import Path
from typing import Any

import orjson as json


def _lock_file_fd(lock_fd: int) -> None:
    """以跨平台方式鎖定 lock file descriptor。"""
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_fd, fcntl.LOCK_EX)


def _unlock_file_fd(lock_fd: int) -> None:
    """以跨平台方式解鎖 lock file descriptor。"""
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _write_json_atomic(path: Path, data: dict[str, Any]):
    """以原子方式將 JSON 內容覆寫到 ``path``。

    目前此函式沒有具語意的回傳值；
    呼叫端若選擇直接透傳回傳結果，可在未來新增成功/失敗回傳契約時
    免於同步調整外層包裝介面。

    使用 fsync 確保資料寫入磁碟，避免作業系統緩衝區未 flush
    就執行 os.replace() 導致資料遺失。
    """
    tmp_path = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)

    # 寫入暫存檔
    tmp_path.write_bytes(json.dumps(data, option=json.OPT_INDENT_2))

    # 確保資料寫入磁碟（Windows 使用 FlushFileBuffers）
    with open(tmp_path, "r+b") as f:
        os.fsync(f.fileno())

    os.replace(tmp_path, path)


def _get_active_shard_path(
    *,
    type_dir: Path,
    cache_type: str,
    active_shard_file: str,
) -> Path:
    """取得目前作用中的分片檔路徑，必要時初始化 `.active` 指標。"""
    active_file = type_dir / active_shard_file

    if not active_file.exists():
        pat = re.compile(rf"^{re.escape(cache_type)}_(\d+)\.json$", re.IGNORECASE)
        existing_shards: list[int] = []
        for f in type_dir.glob(f"{cache_type}_*.json"):
            m = pat.match(f.name)
            if m:
                existing_shards.append(int(m.group(1)))

        latest_id = max(existing_shards or [1])
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
    """當目前分片容量達上限時切到下一片，並回傳是否有旋轉。

    使用檔案鎖確保旋轉操作的原子性，防止 TOCTOU Race Condition。
    """
    if len(data) < rolling_shard_size:
        return False

    active_file = type_dir / active_shard_file
    lock_file = type_dir / f"{active_shard_file}.lock"

    # 建立 lock 檔並取得獨占鎖，防止 TOCTOU race
    type_dir.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
    try:
        # 以跨平台 file lock 進行檔案鎖定
        _lock_file_fd(lock_fd)

        # 再次確認容量（防止鎖競爭期間已被其他程序旋轉）
        if len(data) < rolling_shard_size:
            return False

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
    finally:
        _unlock_file_fd(lock_fd)
        os.close(lock_fd)


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
    """把多筆條目分段寫入 active shard，必要時自動切片。

    使用檔案鎖確保讀取-修改-寫入循環的原子性，防止 TOCTOU race。
    """
    if not entries:
        return

    active_file = type_dir / active_shard_file
    lock_file = type_dir / f"{active_shard_file}.lock"

    # 先確保 `.active` 指標檔存在，避免下方分支直接讀取時找不到檔案。
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
            logger.info(f"🔁 {cache_type} 手動切新分片 -> {nxt:05d} (force_new_shard={force_new_shard})")

    pending_items = list(entries.items())
    while pending_items:
        # 建立 lock 檔並取得獨占鎖，防止 TOCTOU race
        type_dir.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
        rotated = False
        try:
            _lock_file_fd(lock_fd)

            # 在鎖保護下讀取 active shard path（避免 TOCTOU）
            save_path = _get_active_shard_path(
                type_dir=type_dir,
                cache_type=cache_type,
                active_shard_file=active_shard_file,
            )

            current_data: dict[str, Any] = {}
            try:
                old_data = json.loads(save_path.read_bytes())
                if isinstance(old_data, dict):
                    current_data = old_data
            except FileNotFoundError:
                current_data = {}
            except Exception as e:
                if logger:
                    logger.warning(f"⚠️ 讀取舊分片失敗，將以空白分片續寫: {e}")

            # 在鎖保護下檢查是否需要旋轉
            if len(current_data) >= rolling_shard_size:
                # 需要旋轉：釋放當前鎖，讓旋轉邏輯取得鎖
                _unlock_file_fd(lock_fd)
                os.close(lock_fd)
                lock_fd = -1

                _rotate_shard_if_needed(
                    type_dir=type_dir,
                    cache_type=cache_type,
                    data=current_data,
                    rolling_shard_size=rolling_shard_size,
                    active_shard_file=active_shard_file,
                    logger=logger,
                )
                rotated = True
                continue  # 重新取得路徑和資料

        finally:
            if lock_fd != -1:
                try:
                    _unlock_file_fd(lock_fd)
                except Exception:
                    pass
                os.close(lock_fd)

        if not rotated:
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
                # 若目前分片已滿，預旋轉到下一片
                _rotate_shard_if_needed(
                    type_dir=type_dir,
                    cache_type=cache_type,
                    data=current_data,
                    rolling_shard_size=rolling_shard_size,
                    active_shard_file=active_shard_file,
                    logger=logger,
                )
