"""app/icon_reader.py 模組。

用途：從 JAR ZIP 內直接讀取 icon PNG bytes，LRU handle cache 避免重複開關。
維護注意：本模組為 ZIP icon 讀取的單一責任入口。
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import zipfile


@dataclass(frozen=True)
class IconRef:
    """JAR 內 icon 的 URI 參照（frozen dataclass）。

    URI 格式：jar://<jar_rel_path>:<png_path>
    - jar_rel_path：相對於 source_root 的 JAR 路徑
    - png_path：ZIP 內部的 PNG 資源路徑

    向後相容：舊磁碟路徑 parse 回傳 None。
    """

    jar_path: Path       # JAR 的相對路徑（相對於 source_root）
    png_path: str        # ZIP 內部路徑

    @staticmethod
    def parse(uri: str) -> "IconRef | None":
        """從 URI 字串解析。舊磁碟路徑視為無 icon，回傳 None。"""
        if not uri.startswith("jar://"):
            return None
        _, rest = uri.split("jar://", 1)
        # Windows 路徑含 C:\，只能用 rsplit(":", 1) 取最後一個 :
        jar, png = rest.rsplit(":", 1)
        # 剝離 query string（如果有）
        png = png.split("?")[0]
        return IconRef(Path(jar), png)

    def to_uri(self) -> str:
        """序列化為 URI 字串。"""
        return f"jar://{self.jar_path}:{self.png_path}"


@lru_cache(maxsize=32)
def _get_zipfile(jar_path: Path) -> zipfile.ZipFile:
    """LRU cached ZIP handle（最多 32 個 open handles）。

    避免同一個 JAR 連續讀取時重複開關 ZIP。
    Minecraft UI 行為：同 mod 的 icon 連續出現，LRU 自然命中率高。
    """
    return zipfile.ZipFile(jar_path, "r")


def read_icon_bytes(jar_path: Path, png_path: str) -> bytes | None:
    """從 JAR ZIP 讀取 icon PNG bytes。

    使用 LRU cache 管理 ZIP handle，確保同一個 JAR 只開一次 ZIP。

    參數：
        jar_path：JAR 檔案的絕對或相對路徑（Path 物件）
        png_path：ZIP 內部的 PNG 資源路徑

    回傳：
        PNG bytes，或 None（讀取失敗）
    """
    try:
        zf = _get_zipfile(jar_path)
        return zf.read(png_path)
    except Exception:
        return None
