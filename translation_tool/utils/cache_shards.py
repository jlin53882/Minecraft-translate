"""translation_tool/utils/cache_shards.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
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


# 分片新舊順序紀錄（freshness contract）：
#   每次寫入任何分片（編號分片或時間戳分片）時，在同一資料夾的 SHARD_ORDER_FILE
#   記錄「檔名 -> 單調遞增的寫入序號」。載入時依序號由舊到新排序，後者覆蓋前者，
#   因此同一 key 最後一次寫入的值一定勝出，與檔名排序無關。
#   沒有紀錄的舊分片（升級前產生）序號視為 0，彼此維持檔名排序，且早於所有有紀錄的分片。
#   刻意不用 .json 副檔名，避免被 `*.json` 的載入 / 指紋 glob 當成分片。
SHARD_ORDER_FILE = ".shard_order"


@contextmanager
def _shard_lock(type_dir: Path, active_shard_file: str) -> Iterator[None]:
    """取得與 `.active` 旋轉相同的跨程序獨占鎖。"""
    type_dir.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(
        str(type_dir / f"{active_shard_file}.lock"), os.O_CREAT | os.O_RDWR
    )
    try:
        _lock_file_fd(lock_fd)
        yield
    finally:
        try:
            _unlock_file_fd(lock_fd)
        finally:
            os.close(lock_fd)


def _read_shard_order(type_dir: Path) -> dict[str, int]:
    """讀取分片寫入序號；檔案不存在或損毀時視為沒有紀錄。"""
    try:
        raw = json.loads((type_dir / SHARD_ORDER_FILE).read_bytes())
    except FileNotFoundError:
        return {}
    except Exception:
        logging.getLogger(__name__).warning(
            "讀取分片序號失敗，視為沒有紀錄：%s", type_dir, exc_info=True
        )
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, int)}


def _record_shard_write(type_dir: Path, shard_name: str) -> None:
    """把 shard_name 標記為目前最新寫入（呼叫端須持有 `_shard_lock`）。"""
    order = _read_shard_order(type_dir)
    order[shard_name] = max(order.values(), default=0) + 1
    _write_json_atomic(type_dir / SHARD_ORDER_FILE, order)


def list_shards_oldest_first(type_dir: Path) -> list[Path]:
    """回傳 type_dir 內所有分片，依寫入先後由舊到新排序（後者覆蓋前者）。"""
    order = _read_shard_order(type_dir)
    return sorted(type_dir.glob("*.json"), key=lambda f: (order.get(f.name, 0), f.name))


def _write_timestamp_shard(
    type_dir: Path,
    cache_type: str,
    entries: dict[str, Any],
    active_shard_file: str = ".active",
) -> Path:
    """把 entries 整批寫入 ``{cache_type}_{mmddHHMMSS}-{seq}.json``，回傳檔案路徑。

    以 O_EXCL 預先建立檔案取得唯一的 seq（同一秒內多次寫入用 -1、-2 區分），
    不會覆蓋既有分片，也不會影響編號分片的 `.active` 指標。
    寫入失敗時會移除預先建立的空檔，避免留下永久的 0-byte 分片。
    新舊順序由 `_record_shard_write` 記錄，不依賴檔名。
    """
    ts = datetime.now().strftime("%m%d%H%M%S")  # noqa: DTZ005 檔名使用本機時間
    with _shard_lock(type_dir, active_shard_file):
        seq = 1
        while True:
            path = type_dir / f"{cache_type}_{ts}-{seq}.json"
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                seq += 1
                continue
            os.close(fd)
            break
        try:
            _write_json_atomic(path, entries)
            _record_shard_write(type_dir, path.name)
        except BaseException:
            path.unlink(missing_ok=True)
            path.with_suffix(".tmp").unlink(missing_ok=True)
            raise
    return path


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


def _advance_active_pointer_locked(
    *,
    type_dir: Path,
    cache_type: str,
    active_shard_file: str,
    logger: logging.Logger | None = None,
) -> str:
    """把 `.active` 指標前進一號並回傳新編號（呼叫端須已持有 `_shard_lock`）。"""
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
    return new_id


def force_rotate_active_shard(
    *,
    type_dir: Path,
    cache_type: str,
    active_shard_file: str,
    logger: logging.Logger | None = None,
) -> str:
    """手動把 active shard 切到下一片（在 `.active.lock` 保護下，與寫入交易互斥）。"""
    with _shard_lock(type_dir, active_shard_file):
        return _advance_active_pointer_locked(
            type_dir=type_dir,
            cache_type=cache_type,
            active_shard_file=active_shard_file,
            logger=logger,
        )


def _rotate_shard_if_needed_locked(
    *,
    type_dir: Path,
    cache_type: str,
    data: dict[str, Any],
    rolling_shard_size: int,
    active_shard_file: str,
    logger: logging.Logger | None = None,
) -> bool:
    """旋轉 active shard 指標（呼叫端須已持有 `_shard_lock`，此函式不會再取鎖）。"""
    if len(data) < rolling_shard_size:
        return False

    _advance_active_pointer_locked(
        type_dir=type_dir,
        cache_type=cache_type,
        active_shard_file=active_shard_file,
        logger=logger,
    )
    return True


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

    自行取得 `.active.lock`（獨立呼叫用）；已持有鎖的交易內請改用
    `_rotate_shard_if_needed_locked`，避免 non-reentrant 檔案鎖死鎖。
    """
    if len(data) < rolling_shard_size:
        return False

    with _shard_lock(type_dir, active_shard_file):
        return _rotate_shard_if_needed_locked(
            type_dir=type_dir,
            cache_type=cache_type,
            data=data,
            rolling_shard_size=rolling_shard_size,
            active_shard_file=active_shard_file,
            logger=logger,
        )


def _commit_numbered_shard(
    *,
    type_dir: Path,
    save_path: Path,
    new_data: dict[str, Any],
    old_bytes: bytes | None,
) -> None:
    """寫入編號分片並記錄新舊序號（呼叫端須持有 `_shard_lock`）。

    資料與 `.shard_order` 視為同一筆交易：序號更新失敗時把分片還原成寫入前的內容
    （原本不存在則刪除）再拋出例外，不會留下「新資料 + 舊序號」的矛盾狀態。
    """
    _write_json_atomic(save_path, new_data)
    try:
        _record_shard_write(type_dir, save_path.name)
    except BaseException:
        try:
            if old_bytes is None:
                save_path.unlink(missing_ok=True)
            else:
                tmp = save_path.with_suffix(".tmp")
                tmp.write_bytes(old_bytes)
                os.replace(tmp, save_path)
        except Exception:  # noqa: BLE001, S110
            pass  # 還原也失敗時仍以原始例外為準
        raise


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

    # 先確保 `.active` 指標檔存在，避免下方分支直接讀取時找不到檔案。
    _get_active_shard_path(
        type_dir=type_dir,
        cache_type=cache_type,
        active_shard_file=active_shard_file,
    )

    # force_new_shard=True：寫入獨立的時間戳分片，不動 .active 指標。
    if force_new_shard:
        path = _write_timestamp_shard(type_dir, cache_type, entries, active_shard_file)
        if logger:
            logger.info(
                f"💾 {cache_type} saved (timestamp): {path.name} (+{len(entries)})"
            )
        return

    # 防溢：一次寫入的條目數超過分片上限時，整批寫入一個時間戳分片，
    # 避免把單次寫入硬切成多個半滿的編號分片。
    if len(entries) > rolling_shard_size:
        path = _write_timestamp_shard(type_dir, cache_type, entries, active_shard_file)
        if logger:
            logger.warning(
                f"⚠️ {cache_type} overflow ({len(entries)} > {rolling_shard_size}) "
                f"→ timestamp shard: {path.name}"
            )
        return

    # 編號分片的 read → merge → atomic write → 序號更新 全在同一個跨程序交易鎖內，
    # 並行寫入不會 lost update，資料與 .shard_order 也不會分離。
    # 鎖歸屬：此處是唯一 owner；交易內只呼叫 *_locked / 明確要求持鎖的 helper。
    pending_items = list(entries.items())
    while pending_items:
        with _shard_lock(type_dir, active_shard_file):
            save_path = _get_active_shard_path(
                type_dir=type_dir,
                cache_type=cache_type,
                active_shard_file=active_shard_file,
            )

            current_data: dict[str, Any] = {}
            old_bytes: bytes | None = None
            try:
                old_bytes = save_path.read_bytes()
                old_data = json.loads(old_bytes)
                if isinstance(old_data, dict):
                    current_data = old_data
            except FileNotFoundError:
                current_data = {}
            except Exception as e:  # noqa: BLE001
                if logger:
                    logger.warning(f"⚠️ 讀取舊分片失敗，將以空白分片續寫: {e!r}")

            if len(current_data) >= rolling_shard_size:
                _rotate_shard_if_needed_locked(
                    type_dir=type_dir,
                    cache_type=cache_type,
                    data=current_data,
                    rolling_shard_size=rolling_shard_size,
                    active_shard_file=active_shard_file,
                    logger=logger,
                )
                continue  # 重新取得路徑和資料

            capacity = max(0, rolling_shard_size - len(current_data))
            chunk = pending_items[:capacity]

            for k, v in chunk:
                current_data[k] = v

            _commit_numbered_shard(
                type_dir=type_dir,
                save_path=save_path,
                new_data=current_data,
                old_bytes=old_bytes,
            )
            if logger:
                logger.info(
                    f"💾 {cache_type} saved: {save_path.name} (+{len(chunk)} / total={len(current_data)})"
                )

            pending_items = pending_items[capacity:]

            if pending_items:
                # 若目前分片已滿，預旋轉到下一片
                _rotate_shard_if_needed_locked(
                    type_dir=type_dir,
                    cache_type=cache_type,
                    data=current_data,
                    rolling_shard_size=rolling_shard_size,
                    active_shard_file=active_shard_file,
                    logger=logger,
                )
