"""translation_tool/utils/zip_safety.py 模組。

用途：集中處理 ZIP/JAR 的不信任輸入防護。
- read_limited：讀取 ZIP 成員時限制解壓縮後大小（防 ZIP bomb）。
  - 單一成員上限：先看 header 宣告大小，再以實際串流讀取量計數（header 可偽造）。
  - 整包累計上限：傳入 ZipReadBudget，限制同一個 archive 累計解壓縮量與讀取成員數。
- safe_join：組合輸出路徑時確認結果仍在指定根目錄內（防路徑遍歷 / zip-slip）。
  同時檢查字串層級（.. / 絕對路徑）與 symlink / junction 解析後的真實位置。
"""

from __future__ import annotations

import os
import threading
import zipfile

from translation_tool.utils.cancellation import raise_if_cancelled
from translation_tool.utils.log_unit import log_warning

# 文字類資源（lang / json / toml 等）預設上限
MAX_TEXT_BYTES = 10 * 1024 * 1024
# 圖示（png）上限
MAX_ICON_BYTES = 2 * 1024 * 1024
# 整包複製 / 提取時單一檔案上限
MAX_FILE_BYTES = 50 * 1024 * 1024

# 單一 JAR（模組）一次處理的累計上限。
# 只計入「實際被讀取」的成員（lang / model / patchouli 書籍等），不是 JAR 內全部檔案；
# 正常模組遠低於此值，惡意的「大量合法大小成員」則會被擋下。
# 同時有 8 條執行緒各自處理一個 JAR，因此上限同時也約束最壞情況的記憶體用量。
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000

# 圖示索引 / 預覽會對「每個 lang key」各讀一次 model JSON（大型模組有上萬個 key），
# 讀取次數遠多於一般處理，成員數上限放寬；累計位元組上限維持不變。
ICON_SCAN_ARCHIVE_MEMBERS = 200_000

# 整包資源包 / 模組包 ZIP（語言合併會複製其中所有內容檔）的累計上限，較寬鬆。
PACK_ARCHIVE_BYTES = 1024 * 1024 * 1024
PACK_ARCHIVE_MEMBERS = 50_000

_CHUNK = 64 * 1024


class ZipSizeError(RuntimeError):
    """ZIP 成員解壓縮後大小超過安全上限。"""


class ArchiveBudgetError(ZipSizeError):
    """同一個 archive 累計解壓縮量或讀取成員數超過安全上限。

    是 ZipSizeError 的子類：只想統一處理「大小類安全拒絕」的呼叫端可以只捕捉
    ZipSizeError；需要整包中止時再單獨捕捉本類別。
    """


class UnsafePathError(ValueError):
    """輸出路徑會落在指定根目錄之外（含經由 symlink / junction 逃出）。"""


class ZipReadBudget:
    """同一個 archive 的累計讀取預算（執行緒安全，可由多個 worker 共用）。

    用法：處理單一 archive 時建立一個 budget，並傳給該次處理的每個 read_limited。
    超過上限時拋出 ArchiveBudgetError，並對同一個 budget 只記錄一次警告。

    用盡狀態是 sticky 的：任何一次 archive 層級的超限（位元組或成員數）之後，
    同一個 budget 的所有讀取都持續拋出 ArchiveBudgetError（包含之後較小的成員、
    以及其他 worker 正在進行中的讀取），直到這個 budget 物件被丟棄。
    不依賴 used_bytes / used_members 的數值來維持這個狀態。
    """

    def __init__(
        self,
        max_bytes: int = MAX_ARCHIVE_BYTES,
        max_members: int = MAX_ARCHIVE_MEMBERS,
        label: str = "",
    ) -> None:
        self.max_bytes = max_bytes
        self.max_members = max_members
        self.label = label
        self.used_bytes = 0
        self.used_members = 0
        self._lock = threading.Lock()
        self._warned = False

    @classmethod
    def for_icon_scan(cls, label: str = "") -> ZipReadBudget:
        """圖示索引 / 預覽用：每個 lang key 都會讀 model JSON，讀取次數上限較高。"""
        return cls(MAX_ARCHIVE_BYTES, ICON_SCAN_ARCHIVE_MEMBERS, label)

    @classmethod
    def for_pack(cls, label: str = "") -> ZipReadBudget:
        """整包資源包 / 模組包 ZIP 用的較寬鬆預算。"""
        return cls(PACK_ARCHIVE_BYTES, PACK_ARCHIVE_MEMBERS, label)

    @property
    def exhausted(self) -> bool:
        """是否已超過上限（呼叫端可據此提早結束迴圈）。"""
        return self._warned

    def _fail(self, reason: str) -> None:
        """記錄（每個 budget 僅一次）並拋出 ArchiveBudgetError。呼叫時需持有鎖。"""
        where = f"（{self.label}）" if self.label else ""
        msg = f"ZIP 累計讀取超過安全上限{where}：{reason}，拒絕繼續讀取以防止 ZIP bomb 攻擊。"
        if not self._warned:
            self._warned = True
            log_warning(msg)
        raise ArchiveBudgetError(msg)

    def begin_member(self, name: str, declared_size: int) -> None:
        """開始讀取一個成員前呼叫：檢查成員數，並以宣告大小快速拒絕。"""
        with self._lock:
            if self._warned:  # 已用盡：持續拒絕，即使這個成員很小
                self._fail("累計讀取預算已用盡")
            if self.used_members + 1 > self.max_members:
                self._fail(f"讀取成員數超過 {self.max_members}（{name}）")
            if self.used_bytes + declared_size > self.max_bytes:
                self._fail(
                    f"累計解壓縮量將超過 {self.max_bytes / 1024 / 1024:.0f}MB（{name}）"
                )
            self.used_members += 1

    def charge(self, nbytes: int, name: str) -> None:
        """記入實際解壓縮出的位元組數（header 偽造時仍能擋下）。"""
        with self._lock:
            if self._warned:  # 其他 worker 已用盡預算：進行中的讀取也要中止
                self._fail("累計讀取預算已用盡")
            self.used_bytes += nbytes
            if self.used_bytes > self.max_bytes:
                self._fail(
                    f"累計解壓縮量超過 {self.max_bytes / 1024 / 1024:.0f}MB（{name}）"
                )


def read_limited(
    zf: zipfile.ZipFile,
    member: str | zipfile.ZipInfo,
    max_bytes: int = MAX_TEXT_BYTES,
    budget: ZipReadBudget | None = None,
) -> bytes:
    """讀取 ZIP 成員，解壓縮後超過 max_bytes 時拋出 ZipSizeError。

    先檢查 header 宣告的 file_size 以快速拒絕；再以分塊讀取實際計數，
    避免 header 被偽造時仍一次把整個成員解壓進記憶體。
    傳入 budget 時，額外限制同一 archive 的累計解壓縮量與讀取成員數
    （超過拋出 ArchiveBudgetError）。
    """
    info = member if isinstance(member, zipfile.ZipInfo) else zf.getinfo(member)
    if info.file_size > max_bytes:
        raise ZipSizeError(
            f"ZIP 成員 {info.filename} 解壓縮後大小"
            f"（{info.file_size / 1024 / 1024:.1f}MB）超過安全上限"
            f"（{max_bytes / 1024 / 1024:.0f}MB），拒絕讀取以防止 ZIP bomb 攻擊。"
        )
    if budget is not None:
        budget.begin_member(info.filename, info.file_size)

    chunks: list[bytes] = []
    total = 0
    with zf.open(info) as f:
        while True:
            raise_if_cancelled()
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ZipSizeError(
                    f"ZIP 成員 {info.filename} 實際解壓縮大小超過安全上限"
                    f"（{max_bytes / 1024 / 1024:.0f}MB），拒絕讀取以防止 ZIP bomb 攻擊。"
                )
            if budget is not None:
                budget.charge(len(chunk), info.filename)
            chunks.append(chunk)
    return b"".join(chunks)


def _is_within(root: str, path: str) -> bool:
    """path 是否位於 root 內（以 commonpath 判斷，不是字串前綴）。"""
    try:
        root_norm = os.path.normcase(os.path.abspath(root))
        path_norm = os.path.normcase(os.path.abspath(path))
        return os.path.commonpath([root_norm, path_norm]) == root_norm
    except ValueError:  # 不同磁碟機
        return False


def safe_join(root: str | os.PathLike[str], *parts: str) -> str:
    """組合 root 與 parts，結果必須仍位於 root 內，否則拋出 UnsafePathError。

    兩層檢查：
    1. 字串層級：擋下 ``..``、絕對路徑（``os.path.join`` 會丟棄前面的 root）、
       Windows 磁碟機代號、``/out`` 與 ``/out_evil`` 這類共用前綴的目錄。
    2. 檔案系統層級：以 ``os.path.realpath`` 解析既有的 symlink / junction
       （Windows 的 junction 是 reparse point，realpath 會解析）後再檢查一次，
       避免 ``root/assets/link -> /outside`` 這類既有連結讓寫入逃出 root。
       realpath 在非嚴格模式下對「尚不存在的檔案」只解析其存在的前綴、
       其餘部分照字串處理，因此目標檔或上層目錄尚未建立時行為仍合理；
       root 本身含連結時，root 與 target 以同樣方式解析，不會誤判。

    回傳字串層級正規化後的路徑（不是解析後的真實路徑），以維持呼叫端既有的路徑契約。
    注意：這是「寫入前」的檢查，不處理檢查與實際開檔之間被外部改動的競態。
    """
    root_abs = os.path.abspath(os.fspath(root))
    target = os.path.abspath(os.path.join(root_abs, *parts))
    if not _is_within(root_abs, target):
        raise UnsafePathError(f"路徑 {parts!r} 不在 {root_abs} 內")

    real_root = os.path.realpath(root_abs)
    real_target = os.path.realpath(target)
    if not _is_within(real_root, real_target) and _has_link_between(root_abs, target):
        raise UnsafePathError(
            f"路徑 {parts!r} 經由符號連結或 junction 解析後位於 {real_root} 之外"
        )
    return target


def _has_link_between(root: str, target: str) -> bool:
    """root 到 target 之間（含 target）已存在的路徑成分是否有 symlink / junction。

    realpath 解析結果與 root 不一致時，用來分辨「真的有連結逃出 root」與
    「沒有任何連結、只是路徑正規化差異」。後者常見於 OneDrive 同步資料夾
    （雲端檔案是 reparse point，多執行緒建立資料夾時 realpath 可能回傳不同形式），
    不應被當成逃逸；只有實際碰到連結才拒絕。
    """
    rel = os.path.relpath(target, root)
    current = root
    for part in rel.split(os.sep):
        if part in ("", "."):
            continue
        current = os.path.join(current, part)
        if os.path.islink(current) or os.path.isjunction(current):
            return True
    return False
