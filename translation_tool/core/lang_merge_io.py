"""translation_tool/core/lang_merge_io.py 模組。

用途：統一的檔案讀取介面，同時支援 ZIP 和資料夾兩種來源。
"""

from __future__ import annotations

import os
import zipfile
from abc import ABC, abstractmethod
from typing import Any

import orjson as json

from ..utils.cancellation import raise_if_cancelled
from ..utils.log_unit import log_warning
from ..utils.zip_safety import (
    MAX_FILE_BYTES,
    UnsafePathError,
    ZipReadBudget,
    read_limited,
    safe_join,
)


class DirReader(ABC):
    """統一的目錄讀取介面。"""

    @abstractmethod
    def read_bytes(self, rel_path: str) -> bytes:
        """讀取指定相對路徑的檔案內容（原始 bytes）。"""
        ...

    @abstractmethod
    def list_all(self) -> list[str]:
        """回傳所有檔案的相對路徑列表。"""
        ...

    @abstractmethod
    def exists(self, rel_path: str) -> bool:
        """檢查指定相對路徑是否存在。"""
        ...

    def read_text(self, rel_path: str) -> str:
        """讀取並解碼為文字（UTF-8，支援 BOM）。"""
        raw = self.read_bytes(rel_path)
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                return raw.decode("gbk")
            except UnicodeDecodeError:
                return raw.decode("utf-8", errors="replace")

    def read_json(self, rel_path: str) -> dict[str, Any]:
        """讀取並解析為 JSON（失敗拋 RuntimeError）。"""
        text = self.read_text(rel_path)
        if not text:
            return {}
        cleaned = text.strip().lstrip("\ufeff")
        if not cleaned:
            return {}
        try:
            return json.loads(cleaned)
        except Exception as e:
            raise RuntimeError(f"無法讀取 JSON: {rel_path}") from e

    def copy_to(self, rel_path: str, target_path: str) -> None:
        """複製檔案內容至目標路徑。"""
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        raw = self.read_bytes(rel_path)
        with open(target_path, "wb") as f:
            f.write(raw)


class ZipReader(DirReader):
    """ZIP 檔案讀取器。

    budget：同一個 ZIP 的累計讀取預算。語言合併會為每個任務各建一個 ZipReader，
    所以需由呼叫端建立一個 ZipReadBudget 並傳給所有 ZipReader 共用才有累計效果。
    """

    def __init__(self, zf: zipfile.ZipFile, budget: ZipReadBudget | None = None):
        self._zf = zf
        self._budget = budget

    def read_bytes(self, rel_path: str) -> bytes:
        return read_limited(self._zf, rel_path, MAX_FILE_BYTES, budget=self._budget)

    def list_all(self) -> list[str]:
        return self._zf.namelist()

    def exists(self, rel_path: str) -> bool:
        try:
            self._zf.getinfo(rel_path)
            return True
        except KeyError:
            return False


class FolderReader(DirReader):
    """資料夾讀取器。"""

    def __init__(self, root_dir: str):
        self._root = root_dir

    def _full(self, rel_path: str) -> str:
        return os.path.join(self._root, rel_path)

    def read_bytes(self, rel_path: str) -> bytes:
        path = self._full(rel_path)
        with open(path, "rb") as f:
            return f.read()

    def iter_all(self):
        """逐筆列出所有檔案的相對路徑；每進入一個資料夾檢查一次取消。

        來源有幾十萬個檔案（或網路／慢速磁碟）時，完整 ``os.walk`` 本身就可能跑很久；
        先把整個清單建好才檢查取消，使用者按取消後要等掃完才有反應。
        """
        scanned = 0
        for root, _dirs, files in os.walk(self._root):
            raise_if_cancelled()
            for file in files:
                scanned += 1
                if scanned % 256 == 0:  # 單一資料夾有幾十萬個檔案時也要能中斷
                    raise_if_cancelled()
                full = os.path.join(root, file)
                rel = os.path.relpath(full, self._root)
                yield rel.replace("\\", "/")

    def list_all(self) -> list[str]:
        return list(self.iter_all())

    def exists(self, rel_path: str) -> bool:
        return os.path.isfile(self._full(rel_path))


def quarantine_copy(
    reader: DirReader,
    rel_path: str,
    output_dir: str,
    reason: str,
    extra_text=None,
    *,
    errordata_dir: str | None = None,
) -> None:
    """將解析失敗的檔案隔離複製至 errordata 目錄。支援 ZIP 與資料夾兩種 reader。"""
    from ..utils.config_manager import load_config

    if errordata_dir:
        quarantine_root = errordata_dir
    else:
        quarantine_root_name = (
            load_config()
            .get("lang_merger", {})
            .get("quarantine_folder_name", "skipped_json")
        )
        quarantine_root = os.path.join(output_dir, quarantine_root_name)
    try:
        target_path = safe_join(quarantine_root, rel_path)
    except UnsafePathError:
        return

    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    try:
        raw_bytes = reader.read_bytes(rel_path)
        with open(target_path, "wb") as f:
            f.write(raw_bytes)

        reason_path = target_path + ".reason.txt"
        with open(reason_path, "w", encoding="utf-8") as f:
            f.write(reason)

        if extra_text:
            detail_path = target_path + ".detail.txt"
            with open(detail_path, "w", encoding="utf-8") as f:
                f.write(extra_text)
    except Exception as exc:  # noqa: BLE001 - 隔離副本寫入失敗不可中斷合併，但要留下紀錄
        log_warning(
            f"隔離檔案寫入失敗（原檔未被複製或說明檔缺漏）：{target_path}: {exc!r}"
        )
