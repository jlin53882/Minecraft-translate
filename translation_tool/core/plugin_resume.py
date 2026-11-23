"""舊版 FTB／KubeJS／MD 翻譯殘留的續跑標記：只讀取與清除，不再寫入。

目前沒有任何流程會寫出這些標記；這個模組只讓啟動時的續跑對話框能辨識、驗證與清除
使用者磁碟上殘留的舊標記。

- 標記檔：``<資料根目錄>/logs/translator_<kind>_checkpoint.json``（版本 2，含 ``kind``、
  ``options``、涵蓋全部來源檔案的指紋）。
- 指紋是來源檔案內容的雜湊（不用 mtime）；排除輸出資料夾。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from translation_tool.utils.app_paths import get_data_root
from translation_tool.utils.log_unit import log_info, log_warning

# Kinds currently shown by the resume dialog. Historical KubeJS/FTB marker
# names stay separate so cleanup does not make those flows appear resumable.
PLUGIN_KINDS = ("md",)
KNOWN_MARKER_KINDS = ("md", "kubejs", "ftbquests")

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
        from translation_tool.core.ftb_translator_export import (
            resolve_ftbquests_quests_root_impl,
        )

        base = Path(resolve_ftbquests_quests_root_impl(input_dir))
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






def discard_marker(kind: str) -> None:
    """使用者放棄任務時刪除標記；失敗會拋出 ``OSError``，讓對話框保留該任務並顯示錯誤。"""
    marker_path(kind).unlink(missing_ok=True)


def read_marker(kind: str) -> dict[str, Any] | None:
    """讀取標記；不存在回傳 None，損毀的檔案隔離成 ``.corrupt``（與機器翻譯的標記一致）。"""

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
