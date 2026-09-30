"""translation_tool/utils/zip_safety.py 模組。

用途：集中處理 ZIP/JAR 的不信任輸入防護。
- read_limited：讀取 ZIP 成員時限制解壓縮後大小（防 ZIP bomb）。
- safe_join：組合輸出路徑時確認結果仍在指定根目錄內（防路徑遍歷 / zip-slip）。
"""

from __future__ import annotations

import os
import zipfile

# 文字類資源（lang / json / toml 等）預設上限
MAX_TEXT_BYTES = 10 * 1024 * 1024
# 圖示（png）上限
MAX_ICON_BYTES = 2 * 1024 * 1024
# 整包複製 / 提取時單一檔案上限
MAX_FILE_BYTES = 50 * 1024 * 1024

_CHUNK = 64 * 1024


class ZipSizeError(RuntimeError):
    """ZIP 成員解壓縮後大小超過安全上限。"""


class UnsafePathError(ValueError):
    """輸出路徑會落在指定根目錄之外。"""


def read_limited(
    zf: zipfile.ZipFile,
    member: str | zipfile.ZipInfo,
    max_bytes: int = MAX_TEXT_BYTES,
) -> bytes:
    """讀取 ZIP 成員，解壓縮後超過 max_bytes 時拋出 ZipSizeError。

    先檢查 header 宣告的 file_size 以快速拒絕；再以分塊讀取實際計數，
    避免 header 被偽造時仍一次把整個成員解壓進記憶體。
    """
    info = member if isinstance(member, zipfile.ZipInfo) else zf.getinfo(member)
    if info.file_size > max_bytes:
        raise ZipSizeError(
            f"ZIP 成員 {info.filename} 解壓縮後大小"
            f"（{info.file_size / 1024 / 1024:.1f}MB）超過安全上限"
            f"（{max_bytes / 1024 / 1024:.0f}MB），拒絕讀取以防止 ZIP bomb 攻擊。"
        )

    chunks: list[bytes] = []
    total = 0
    with zf.open(info) as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ZipSizeError(
                    f"ZIP 成員 {info.filename} 實際解壓縮大小超過安全上限"
                    f"（{max_bytes / 1024 / 1024:.0f}MB），拒絕讀取以防止 ZIP bomb 攻擊。"
                )
            chunks.append(chunk)
    return b"".join(chunks)


def safe_join(root: str | os.PathLike[str], *parts: str) -> str:
    """組合 root 與 parts，結果必須仍位於 root 內，否則拋出 UnsafePathError。

    處理 ``..`` 片段、絕對路徑（``os.path.join`` 會丟棄前面的 root）、
    Windows 磁碟機代號等情形。只做路徑字串正規化，不解析 symlink。
    """
    root_abs = os.path.abspath(os.fspath(root))
    target = os.path.abspath(os.path.join(root_abs, *parts))
    try:
        common = os.path.commonpath([root_abs, target])
    except ValueError:  # 不同磁碟機
        raise UnsafePathError(f"路徑 {parts!r} 不在 {root_abs} 內") from None
    if common != root_abs:
        raise UnsafePathError(f"路徑 {parts!r} 不在 {root_abs} 內")
    return target
