"""translation_tool/core/lang_merge_io.py 模組。

用途：統一的檔案讀取介面，同時支援 ZIP 和資料夾兩種來源。
"""

from __future__ import annotations

import os
import zipfile
from abc import ABC, abstractmethod
from typing import Any, Dict

import orjson as json


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

    def read_json(self, rel_path: str) -> Dict[str, Any]:
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
        import shutil
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        raw = self.read_bytes(rel_path)
        with open(target_path, "wb") as f:
            f.write(raw)


class ZipReader(DirReader):
    """ZIP 檔案讀取器。"""

    def __init__(self, zf: zipfile.ZipFile):
        self._zf = zf

    def read_bytes(self, rel_path: str) -> bytes:
        return self._zf.read(rel_path)

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

    def list_all(self) -> list[str]:
        result: list[str] = []
        for root, dirs, files in os.walk(self._root):
            for file in files:
                full = os.path.join(root, file)
                rel = os.path.relpath(full, self._root)
                result.append(rel.replace("\\", "/"))
        return result

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
    target_path = os.path.join(quarantine_root, rel_path)

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
    except Exception:
        pass