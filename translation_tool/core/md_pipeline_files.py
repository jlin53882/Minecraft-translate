"""Markdown 管線中間檔（pending／譯後 JSON）的生命週期與注入前驗證。

用途：
  - 原子寫入 pending JSON，不留下半個檔案
  - 只清理「確定是本管線產生」的過期 pending（schema 為 md_pending_blocks_v1）
  - Step 3 注入前驗證譯後 JSON：是否完成、是否仍對應現行 pending、原始檔對應區塊是否被改過
  - 路徑安全：source_md／輸出路徑不得越出各自的根目錄

維護注意：這裡只判斷「可不可以用」，不改變翻譯內容；注入細節仍在 md_inject_qa。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

PENDING_SCHEMA = "md_pending_blocks_v1"
MANIFEST_NAME = "_manifest.json"

# 譯後 JSON 被略過的原因（Step 3 統計用）
REASON_INCOMPLETE = "incomplete"
REASON_PENDING_MISMATCH = "pending_mismatch"
REASON_STALE_SOURCE = "stale_source"


def read_pending_schema_doc(path: Path) -> dict[str, Any] | None:
    """讀取並回傳符合 md_pending_blocks_v1 的 JSON；其他檔案（預覽、翻譯記錄、壞檔）回傳 None。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("schema") == PENDING_SCHEMA:
        return data
    return None


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """先寫暫存檔再 os.replace；暫存檔副檔名不是 .json，不會被 rglob('*.json') 當成資料掃到。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def remove_stale_pending(pending_root: Path, keep: set[Path]) -> int:
    """刪除 pending_root 下「本輪沒有重寫」的 md_pending_blocks_v1 JSON，回傳刪除數。

    只刪 schema 符合的檔案：人工放入、其他工具產生的 JSON 與 manifest 不會被動到。
    """
    removed = 0
    for path in sorted(pending_root.rglob("*.json")):
        if path.name == MANIFEST_NAME or path in keep:
            continue
        if read_pending_schema_doc(path) is None:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed += 1
        _remove_empty_parents(path.parent, pending_root)
    return removed


def _remove_empty_parents(directory: Path, stop_at: Path) -> None:
    while directory != stop_at and stop_at in directory.parents:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent


def is_safe_relative_path(rel: str) -> bool:
    """相對路徑字面檢查：不得是絕對路徑、帶磁碟機代號，或含 .. 片段。

    刻意不用 resolve()：輸入資料夾內含 symlink／junction 的整合包是合法結構。
    """
    text = str(rel or "").strip()
    if not text:
        return False
    for pure in (PurePosixPath(text), PureWindowsPath(text)):
        if pure.is_absolute() or pure.drive or ".." in pure.parts:
            return False
    return True


def is_inside(root: Path, target: Path) -> bool:
    """target 是否位於 root 之內（正規化後比較，Windows 不分大小寫）。"""
    root_text = os.path.normcase(os.path.abspath(root))
    target_text = os.path.normcase(os.path.abspath(target))
    try:
        return os.path.commonpath([root_text, target_text]) == root_text
    except ValueError:
        return False


def item_signature(doc: dict[str, Any]) -> list[tuple[Any, ...]]:
    """區塊身分序列：(id, content_hash, start_line, end_line)。"""
    return [
        (
            it.get("id"),
            it.get("content_hash"),
            it.get("start_line"),
            it.get("end_line"),
        )
        for it in doc.get("items", [])
        if isinstance(it, dict)
    ]


def verify_translated_doc(
    doc: dict[str, Any],
    *,
    pending_doc: dict[str, Any] | None,
    source_lines: list[str],
    content_hash_fn: Callable[[str], str] | None,
) -> str | None:
    """回傳不可注入的原因；None 表示可以注入。

    - incomplete：Step 2 沒有完整完成（失敗、額度用完、取消）卻留下了半成品。
      沒有 step2_status 欄位的舊檔無法判斷，視為可用（來源驗證仍會把關）。
    - pending_mismatch：找不到對應的現行 pending，或區塊身分序列不同
      （來源刪除、語言過濾模式改變、zh_tw 對照檔後來已翻好等）。
    - stale_source：原始檔對應行範圍的內容與抽取當時不同（行號可能已漂移）。
    """
    status = doc.get("step2_status")
    if status is not None and str(status).upper() != "DONE":
        return REASON_INCOMPLETE
    if pending_doc is None or item_signature(doc) != item_signature(pending_doc):
        return REASON_PENDING_MISMATCH
    if content_hash_fn is None:
        return None
    for it in doc.get("items", []):
        if not _block_matches_source(it, source_lines, content_hash_fn):
            return REASON_STALE_SOURCE
    return None


def _block_matches_source(
    item: dict[str, Any],
    source_lines: list[str],
    content_hash_fn: Callable[[str], str],
) -> bool:
    try:
        start = int(item["start_line"])
        end = int(item["end_line"])
    except (KeyError, TypeError, ValueError):
        return False
    if start < 1 or end < start or end > len(source_lines):
        return False
    block_text = "\n".join(source_lines[start - 1 : end]).strip()
    return content_hash_fn(block_text) == item.get("content_hash")
