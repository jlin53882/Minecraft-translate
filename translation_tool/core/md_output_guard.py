"""Markdown 完成資料夾的「人工修改保護」。

Step 3 每寫一個完成檔就記下內容雜湊；下次要覆寫同一個檔時，若磁碟上的內容與
上次寫入的雜湊不同，代表使用者手動改過，這次不覆蓋，留給使用者決定
（刪除該檔即可讓它重新產生）。

沒有紀錄的既有檔案（舊版產生的）視為工具產物，照常覆寫並開始記錄。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

MANIFEST_NAME = ".md_output_hashes.json"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _key(out_rel: str) -> str:
    # Windows 路徑不分大小寫：以小寫、正斜線作為紀錄鍵
    return out_rel.replace("\\", "/").lower()


class OutputGuard:
    """記錄並檢查 final 資料夾內由 Step 3 寫出的檔案。"""

    def __init__(self, final_dir: Path) -> None:
        self._path = final_dir / MANIFEST_NAME
        self._hashes: dict[str, str] = {}
        self._dirty = False
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            raw = data.get("files") if isinstance(data, dict) else None
            if isinstance(raw, dict):
                self._hashes = {str(k): v for k, v in raw.items() if isinstance(v, str)}
        except (OSError, ValueError):
            self._hashes = {}  # 紀錄不存在或損毀：全部視為沒有紀錄

    def is_manually_edited(self, out_rel: str, out_path: Path) -> bool:
        """輸出檔存在、有紀錄，且現在的內容與上次寫入的不同。"""
        recorded = self._hashes.get(_key(out_rel))
        if recorded is None or not out_path.is_file():
            return False
        try:
            return _digest(out_path.read_bytes()) != recorded
        except OSError:
            return False

    def record(self, out_rel: str, out_path: Path) -> None:
        """記錄剛寫完的檔案（讀回實際位元組：換行轉換由平台決定）。"""
        self._hashes[_key(out_rel)] = _digest(out_path.read_bytes())
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        payload = {"schema": "md_output_hashes_v1", "files": self._hashes}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)
        self._dirty = False
