"""FTB／KubeJS／MD 翻譯的「重開後續跑」標記（#164，沿用 #151 的設計）。

與機器翻譯頁相同：已完成批次的譯文在翻譯快取（每批 fsync），標記只是「這個任務沒做完」的
記錄與重新執行所需的選項；續跑 = 以同一組輸入與選項重新執行整條流程，抽取／清理等步驟會
重新推導，翻譯步驟由快取命中已完成的部分，只翻剩下的。

- 標記檔：``<資料根目錄>/logs/translator_<kind>_checkpoint.json``（版本 2，含 ``kind``、
  ``options``、涵蓋全部來源檔案的指紋）。沿用 ``JsonCheckpointAdapter`` 原本的檔名。
- 標記在**整個任務完成**時才清除（FTB 是逐檔案翻譯，不能在第一個檔案完成時就清掉）。
- 指紋是來源檔案內容的雜湊（不用 mtime），在任務開始時計算一次；排除輸出資料夾。
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from translation_tool.utils.app_paths import get_data_root
from translation_tool.utils.log_unit import log_info, log_warning

PLUGIN_KINDS = ("ftbquests", "kubejs", "md")

# 指紋會讀取來源檔案內容；來源資料夾可能是整個 modpack，所以限制檔案種類與規模，
# 超過上限就不提供續跑（不寫標記），而不是花很久雜湊或誤報。
MAX_FINGERPRINT_FILES = 50_000
MAX_FINGERPRINT_BYTES = 512 * 1024 * 1024
_HASH_CHUNK = 1024 * 1024

_FTB_SUFFIXES = (".snbt", ".json", ".qkdownloading")
_KUBEJS_SUFFIXES = (".js", ".json")


def marker_path(kind: str) -> Path:
    """標記檔位置（資料根目錄的 ``logs/``，不依賴工作目錄；#137／#162）。"""
    return get_data_root() / "logs" / f"translator_{kind}_checkpoint.json"


# -- 來源檔案與指紋 -------------------------------------------------------------


def _is_inside(path: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _output_root(input_dir: str, output_dir: str | None) -> Path:
    """各流程的輸出根目錄（預設為 ``<輸入>/Output``）；指紋一律排除它。"""
    base = Path(input_dir).resolve()
    return Path(output_dir).resolve() if output_dir else base / "Output"


def list_source_files(
    kind: str, input_dir: str, output_dir: str | None
) -> list[tuple[str, Path]]:
    """回傳流程實際會讀取的來源檔案：``[(相對路徑, 路徑)]``，排序且不含輸出資料夾。"""
    out_root = _output_root(input_dir, output_dir)
    base = Path(input_dir).resolve()
    files: list[Path]
    if kind == "ftbquests":
        from translation_tool.core.ftb_translator import resolve_ftbquests_quests_root

        base = Path(resolve_ftbquests_quests_root(input_dir))
        files = [
            p
            for p in base.rglob("*")
            if p.is_file() and p.name.lower().endswith(_FTB_SUFFIXES)
        ]
    elif kind == "kubejs":
        from translation_tool.core.kubejs_translator_paths import (
            resolve_kubejs_root_impl,
        )

        base = Path(resolve_kubejs_root_impl(input_dir))
        files = [
            p
            for p in base.rglob("*")
            if p.is_file() and p.name.lower().endswith(_KUBEJS_SUFFIXES)
        ]
    elif kind == "md":
        from translation_tool.plugins.md.md_extract_qa import iter_md_files

        files = list(iter_md_files(base))
    else:
        raise ValueError(f"未知的流程類型：{kind}")
    return sorted(
        (p.relative_to(base).as_posix(), p)
        for p in files
        if not _is_inside(p, out_root)
    )


def compute_source_fingerprint(
    kind: str, input_dir: str, output_dir: str | None
) -> str | None:
    """來源檔案的內容指紋；來源太大、讀取失敗或找不到來源時回傳 None（不提供續跑）。"""
    try:
        entries = list_source_files(kind, input_dir, output_dir)
    except Exception as exc:  # noqa: BLE001 - 失敗代表不提供續跑，流程本身會自己回報輸入問題
        log_info(f"[Resume] 無法列出 {kind} 的來源檔案，本次不記錄續跑標記：{exc!r}")
        return None
    if not entries:
        return None
    if len(entries) > MAX_FINGERPRINT_FILES:
        log_info(f"[Resume] {kind} 來源檔案過多（{len(entries)}），本次不記錄續跑標記")
        return None
    digest = hashlib.sha256(kind.encode("utf-8"))
    total = 0
    try:
        for rel, path in entries:
            digest.update(b"\x1f" + rel.encode("utf-8") + b"\x1e")
            with open(path, "rb") as f:
                while chunk := f.read(_HASH_CHUNK):
                    total += len(chunk)
                    if total > MAX_FINGERPRINT_BYTES:
                        log_info(f"[Resume] {kind} 來源過大，本次不記錄續跑標記")
                        return None
                    digest.update(chunk)
            digest.update(b"\x1d")
    except OSError as exc:
        log_info(f"[Resume] 讀取 {kind} 來源失敗，本次不記錄續跑標記：{exc!r}")
        return None
    return digest.hexdigest()


# -- 任務期間的狀態 -----------------------------------------------------------------


@dataclass
class ResumeContext:
    """一次翻譯任務的續跑資訊；``JsonCheckpointAdapter`` 依 ``kind`` 取得它。"""

    kind: str
    input_dir: str
    output_dir: str | None
    options: dict[str, Any]
    fingerprint: str
    loops_started: int = 0
    loops_completed: int = 0
    completed_base: int = 0  # 已完成迴圈處理的項目數（顯示用的累計進度）
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def loop_started(self) -> None:
        with self._lock:
            self.loops_started += 1

    def loop_completed(self, processed: int = 0) -> None:
        with self._lock:
            self.loops_completed += 1
            self.completed_base += processed

    @property
    def all_loops_done(self) -> bool:
        with self._lock:
            return self.loops_completed >= self.loops_started


_ACTIVE: dict[str, ResumeContext] = {}
_ACTIVE_LOCK = threading.Lock()


def active_context(kind: str) -> ResumeContext | None:
    """目前進行中的任務（同一種流程同時只會有一個，UI 已限制）。"""
    with _ACTIVE_LOCK:
        return _ACTIVE.get(kind)


def clear_marker(kind: str) -> None:
    try:
        marker_path(kind).unlink(missing_ok=True)
    except OSError as exc:
        log_warning(f"[Resume] 無法清除 {kind} 的續跑標記：{exc!r}")


@contextmanager
def resume_task(
    kind: str,
    *,
    input_dir: str,
    output_dir: str | None,
    options: dict[str, Any],
    session: Any = None,
    dry_run: bool = False,
    translate_enabled: bool = True,
    fingerprint_fn: Callable[[str, str, str | None], str | None] | None = None,
) -> Iterator[ResumeContext | None]:
    """包住整個翻譯任務：任務期間讓 adapter 能寫出續跑標記，結束時決定是否清除。

    - dry-run 或沒有勾選翻譯步驟：不記錄標記，也不碰既有標記。
    - 整個任務完成（沒有錯誤、沒有取消、每個翻譯迴圈都 DONE）才清除標記；
      取消、失敗、金鑰耗盡都保留。
    - 無法計算來源指紋（來源太大、讀取失敗）時不提供續跑，不影響翻譯本身。
    """
    if dry_run or not translate_enabled:
        yield None
        return
    compute = fingerprint_fn or compute_source_fingerprint
    fingerprint = compute(kind, input_dir, output_dir)
    if fingerprint is None:
        yield None
        return
    ctx = ResumeContext(kind, input_dir, output_dir, dict(options), fingerprint)
    with _ACTIVE_LOCK:
        _ACTIVE[kind] = ctx
    try:
        yield ctx
    finally:
        with _ACTIVE_LOCK:
            if _ACTIVE.get(kind) is ctx:
                del _ACTIVE[kind]
        failed = bool(getattr(session, "error", False)) or bool(
            getattr(session, "cancel_requested", False)
        )
        if not failed and ctx.all_loops_done:
            clear_marker(kind)


def read_marker(kind: str) -> dict[str, Any] | None:
    """讀取標記；不存在回傳 None，損毀的檔案隔離成 ``.corrupt``（與機器翻譯的標記一致）。"""
    import json

    path = marker_path(kind)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError(f"標記必須是 JSON 物件，實際為 {type(data).__name__}")
        return data
    except Exception as exc:  # noqa: BLE001 - 損毀的標記視為沒有，但要留下紀錄並隔離
        quarantined = f"{path}.corrupt"
        try:
            os.replace(path, quarantined)
            where = f"，已隔離為 {quarantined}"
        except OSError as move_exc:
            where = f"（無法隔離：{move_exc!r}）"
        log_warning(f"[Resume] 讀取 {kind} 標記失敗，視為沒有標記{where}：{exc!r}")
        return None
