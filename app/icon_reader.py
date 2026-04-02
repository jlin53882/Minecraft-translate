"""app/icon_reader.py 模組。

用途：從 JAR ZIP 內直接讀取 icon PNG bytes，LRU handle cache 避免重複開關。
維護注意：本模組為 ZIP icon 讀取的單一責任入口。
"""

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import threading
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
        # 標準化：把路徑中的反斜線轉成正斜線（避免 Path 轉換後的 backslash 破壞解析）
        normalized = uri.replace("\\", "/")
        _, rest = normalized.split("jar://", 1)
        # 防禦：沒有 ":" 就不是有效 URI
        if ":" not in rest:
            return None
        # Windows 路徑含 C:\，只能用 rsplit(":", 1) 取最後一個 :
        jar, png = rest.rsplit(":", 1)
        # 剝離 query string（如果有）
        png = png.split("?")[0]
        return IconRef(Path(jar), png)

    def to_uri(self) -> str:
        """序列化為 URI 字串（永遠輸出 forward-slash 路徑）。"""
        return f"jar://{self.jar_path.as_posix()}:{self.png_path}"


class _ZipCache:
    """手動 LRU cache，確保 evicted handle 會被關閉避免 fd leak。

    實作：OrderedDict + move_to_end，LRU 條目 close 並移除後才加入新條目。
    上限：MAX_SIZE 個 open ZipFile handles。
    """

    MAX_SIZE = 32

    def __init__(self) -> None:
        self._cache: OrderedDict[Path, zipfile.ZipFile] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, jar_path: Path) -> zipfile.ZipFile:
        """取得 ZIP handle，LRU 熱點放在末端。執行緒安全。"""
        with self._lock:
            if jar_path in self._cache:
                # 命中：移動到末端（most recently used）
                self._cache.move_to_end(jar_path)
                return self._cache[jar_path]

            # 未命中：關閉最舊的條目（如果有）
            while len(self._cache) >= self.MAX_SIZE:
                _jar, zf = self._cache.popitem(last=False)  # pop oldest (least recently used)
                try:
                    zf.close()
                except Exception:
                    pass  # 關閉失敗不 blocking

            # 開新 handle 並加入 cache
            zf = zipfile.ZipFile(jar_path, "r")
            self._cache[jar_path] = zf
            return zf

    def close_all(self) -> None:
        """關閉所有快取的 handles 並清除快取（用於測試重置或程式結束）。"""
        with self._lock:
            while self._cache:
                _jar, zf = self._cache.popitem(last=False)
                try:
                    zf.close()
                except Exception:
                    pass


_zip_cache = _ZipCache()


def read_icon_bytes(jar_path: Path, png_path: str) -> bytes | None:
    """從 JAR ZIP 讀取 icon PNG bytes。

    使用 LRU cache 管理 ZIP handle，確保同一個 JAR 只開一次 ZIP。
    evict 時自動關閉 handle，避免 fd leak。

    參數：
        jar_path：JAR 檔案的絕對或相對路徑（Path 物件）
        png_path：ZIP 內部的 PNG 資源路徑

    回傳：
        PNG bytes，或 None（讀取失敗）
    """
    try:
        zf = _zip_cache.get(jar_path)
        return zf.read(png_path)
    except Exception:
        return None
