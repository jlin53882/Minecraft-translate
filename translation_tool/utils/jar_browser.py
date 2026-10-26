"""jar_browser.py - 多執行緒 JAR 掃描工具

提供共用的多執行緒 JAR 讀取工具，封裝錯誤隔離與進度回呼。
供 icon_preview_view、jar_processor_extract 等模組複用。

主要功能：
- 平行掃描多個 JAR 檔案（ThreadPoolExecutor，I/O bound 最佳化）
- 支援多 pattern 正則匹配（一次設定，彈性擴充）
- 每個 JAR 獨立錯誤隔離（bad zip 不影響其他）
- 進度回呼 callback（每個 JAR 完成後通知 caller）
- Binary 檔案（.png 等）UTF-8 decode 失敗時回傳 None，由 caller 自行處理

作者：PR #53 實作
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from translation_tool.utils.bounded_executor import bounded_as_completed
from translation_tool.utils.cancellation import raise_if_cancelled
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_error, log_warning
from translation_tool.utils.nested_archive import walk_nested_archives
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor
from translation_tool.utils.worker_count import resolve_worker_count
from translation_tool.utils.zip_safety import (
    ArchiveBudgetError,
    ZipReadBudget,
    ZipSizeError,
    read_limited,
)


def _get_default_workers() -> int:
    """從 config 讀取 max_workers，若無設定則 fallback 為 CPU 核心數的一半。

    回傳：
        int: 最大執行緒數（至少 1）
    """
    try:
        return resolve_worker_count("jar_browser", load_config())
    except Exception:  # noqa: BLE001, S110 - config 讀取失敗時不 blocking，直接用 fallback
        pass
    return resolve_worker_count("jar_browser", None)


class ScanResults(dict):
    """scan_jars 的回傳值：行為與一般 ``dict[Path, dict[str, str | None]]`` 完全相同，
    另外攜帶每個「被掃描的 JAR」的 ZipReadBudget（``budgets``）。

    預掃描與後續提取處理的是同一個 archive，必須共用同一份累計讀取預算；
    否則兩個階段各拿一份新的預算，實際可讀量會接近預算上限的兩倍。
    ``budgets`` 包含所有被掃描的 JAR（即使掃描結果為空或已超限而不在 dict 本身內），
    讓超限的預算在後續階段持續有效。
    """

    def __init__(self) -> None:
        super().__init__()
        self.budgets: dict[Path, ZipReadBudget] = {}
        self.failed_jars: set[Path] = set()
        self.skipped_jars: set[Path] = set()
        self.entries: dict[Path, list[ScannedEntry]] = {}
        self.nested_jars: dict[Path, int] = {}
        self.skipped_nested: dict[Path, int] = {}
        self.collision_notes: dict[Path, list[str]] = {}


@dataclass(frozen=True)
class ScannedEntry:
    """預掃描找到的資源及其在 outer JAR 內的來源鏈。"""

    logical_path: str
    member_chain: tuple[str, ...]
    content: str | None
    size: int
    raw_data: bytes | None = None

    @property
    def is_nested(self) -> bool:
        return len(self.member_chain) > 1


@dataclass
class _JarScanDetails:
    content: dict[str, str | None]
    entries: list[ScannedEntry]
    nested_jars: int = 0
    skipped_nested: int = 0
    collision_notes: list[str] | None = None
    error: str | None = None


def _scan_single_jar(
    jar_path: Path,
    patterns: list[str],
    budget: ZipReadBudget | None = None,
    failure_callback: Callable[[Path], None] | None = None,
    skipped_callback: Callable[[Path], None] | None = None,
    scan_nested: bool = True,
) -> tuple[Path, dict[str, str | None]]:
    """掃描單一 JAR，符合 pattern 的檔案內容讀取出來。

    這是一個純函式：相同輸入永遠產生相同輸出，無副作用。
    設計給 ThreadPoolExecutor 並行使用。

    參數：
        jar_path: JAR 檔案路徑
        patterns: 要匹配的正規表達式列表

    回傳：
        tuple[Path, dict[str, str | None]]
        - jar_path: 該 JAR 的路徑
        - content: {檔案路徑: 檔案內容或 None}
          - 文字檔：UTF-8 解碼後的字串
          - binary 檔案（UTF-8 decode 失敗）：None（由 caller 自行處理）
    """
    details = _scan_single_jar_detailed(
        jar_path,
        patterns,
        budget=budget,
        failure_callback=failure_callback,
        skipped_callback=skipped_callback,
        scan_nested=scan_nested,
    )
    return jar_path, details.content


def _scan_single_jar_detailed(
    jar_path: Path,
    patterns: list[str],
    budget: ZipReadBudget | None = None,
    failure_callback: Callable[[Path], None] | None = None,
    skipped_callback: Callable[[Path], None] | None = None,
    scan_nested: bool = True,
) -> _JarScanDetails:
    """掃描單一 outer JAR，並保留巢狀資源的來源鏈供抽取階段使用。"""
    result: dict[str, str | None] = {}
    entries: list[ScannedEntry] = []
    collisions: list[str] = []
    nested_jars = 0
    skipped_nested = 0
    if budget is None:
        budget = ZipReadBudget(label=jar_path.name)

    def visit_archive(
        zf: zipfile.ZipFile,
        label: str,
        depth: int,
        archive_chain: tuple[str, ...],
        archive_budget: ZipReadBudget,
    ) -> bool:
        nonlocal nested_jars
        if depth:
            nested_jars += 1
        for info in zf.infolist():
            raise_if_cancelled()
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if not any(re.search(pattern, name) for pattern in patterns):
                continue
            try:
                data = read_limited(zf, info, budget=archive_budget)
            except ArchiveBudgetError:
                raise
            except ZipSizeError as size_err:
                if skipped_callback:
                    skipped_callback(jar_path)
                log_warning(f"[jar_browser] 略過過大檔案 {label}!{name}: {size_err!r}")
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None

            if name in result:
                collisions.append(
                    f"{label}!{name} 與既有來源的 canonical 路徑衝突，保留先出現的來源"
                )
                log_warning("[jar_browser] %s", collisions[-1])
                continue
            result[name] = text
            entries.append(
                ScannedEntry(
                    logical_path=name,
                    member_chain=archive_chain + (name,),
                    content=text,
                    size=info.file_size,
                    # Nested binary resources are already bounded and cached so
                    # extraction does not have to recharge the outer archive
                    # budget merely to reopen the same inner JAR.
                    raw_data=data if archive_chain and text is None else None,
                )
            )
        return True

    def on_nested_error(label: str, name: str, exc: BaseException) -> None:
        nonlocal skipped_nested
        skipped_nested += 1
        log_warning("[jar_browser] 略過內嵌 jar %s：%s", label, exc)

    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            walk_nested_archives(
                zf,
                jar_path.name,
                budget,
                visit_archive,
                budget_factory=lambda _label: budget,
                on_nested_error=on_nested_error,
                scan_nested=scan_nested,
            )
    except zipfile.BadZipFile:
        log_warning(f"[jar_browser] 不是有效的 ZIP/JAR: {jar_path.name}")
        if failure_callback:
            failure_callback(jar_path)
        return _JarScanDetails({}, [], collision_notes=collisions, error="BadZipFile")
    except ArchiveBudgetError as budget_err:
        # 整包累計超限：捨棄部分結果，避免後續把不完整的 JAR 當成成功。
        log_error(
            f"[jar_browser] 略過整個 JAR（累計讀取超過安全上限）: "
            f"{jar_path.name} - {budget_err!r}"
        )
        if failure_callback:
            failure_callback(jar_path)
        return _JarScanDetails(
            {}, [], collision_notes=collisions, error=str(budget_err)
        )
    except Exception as ex:  # noqa: BLE001
        log_error(f"[jar_browser] 讀取失敗: {jar_path.name} - {ex!r}")
        if failure_callback:
            failure_callback(jar_path)
        return _JarScanDetails({}, [], collision_notes=collisions, error=str(ex))
    return _JarScanDetails(
        result,
        entries,
        nested_jars=nested_jars,
        skipped_nested=skipped_nested,
        collision_notes=collisions,
    )


def scan_jars(
    jar_dir: Path,
    patterns: list[str],
    max_workers: int | None = None,
    processed_callback: Callable[[int, int], None] | None = None,
    jar_files: Iterable[Path | str] | None = None,
    scan_nested: bool = True,
) -> dict[Path, dict[str, str | None]]:
    """平行讀取多個 JAR 內符合 pattern 的檔案內容。

    回傳值是 ScanResults（dict 子類），``.budgets`` 帶有各 JAR 的累計讀取預算，
    供提取階段延續使用。

    參數：
        jar_dir: JAR 檔案所在的目錄
        patterns: 要讀取的檔案 pattern（正則表達式），例如：
            - r"assets/([^/]+)/lang/en_us\\.json"   → 翻譯檔
            - r"assets/([^/]+)/icon\\.png"           → Fabric icon
            - r"fabric\\.mod\\.json"                → Fabric metadata
            - r"neoforge\\.mods\\.toml"             → NeoForge metadata
        max_workers: 最大執行緒數（None=從 config 自動讀取）
        processed_callback: 進度回呼 `(processed: int, total: int) -> None`
            每個 JAR 完成後呼叫一次，用於更新進度條等 UI 元件。
        jar_files: 明確指定要掃描的 JAR 清單（可含子目錄內的 JAR）。
            None（預設）維持舊行為：只掃 ``jar_dir`` 頂層的 ``*.jar``。
            提取流程會傳入與 ``find_jar_files`` 相同的清單，讓預掃描與實際處理的
            JAR 一致（issue #111）；此時 ``jar_dir`` 僅作為相容參數，不再用於找檔。
        scan_nested: 是否遞迴掃描 ``META-INF/jarjar``／``META-INF/jars`` 內嵌 JAR。

    回傳：
        dict[Path, dict[str, str | None]]
        {
            jar_path: {
                "assets/modid/lang/en_us.json": "{...json content...}",
                "icon.png": None,  # binary 檔案，decode 失敗
                ...
            }
        }

    範例：
        result = scan_jars(
            jar_dir=Path("mods"),
            patterns=[r"assets/([^/]+)/lang/en_us\\.json"],
        )
        for jar_path, files in result.items():
            en_us = files.get("assets/modid/lang/en_us.json")
    """
    # 找出所有 JAR 檔案：明確清單優先（去重、保留順序）；否則維持只掃頂層的舊行為
    if jar_files is not None:
        jar_files = list(dict.fromkeys(Path(p) for p in jar_files))
    else:
        jar_files = list(jar_dir.glob("*.jar")) if jar_dir.is_dir() else []
    total = len(jar_files)

    # 決定 worker 數量
    workers = max_workers if max_workers is not None else _get_default_workers()

    results = ScanResults()

    # 空目錄或無 JAR 檔：直接回傳空 dict
    if not jar_files:
        return results

    # 每個 JAR 一份預算，由「掃描 → 後續提取」共用（見 ScanResults）；
    # JAR 之間不共用，worker 各自只處理自己的 JAR。
    for jar_path in jar_files:
        results.budgets[jar_path] = ZipReadBudget(label=jar_path.name)

    def submit_scan(executor, jar_path: Path):
        return executor.submit(
            _scan_single_jar_detailed,
            jar_path,
            patterns,
            results.budgets[jar_path],
            results.failed_jars.add,
            results.skipped_jars.add,
            scan_nested,
        )

    with ContextThreadPoolExecutor(max_workers=workers) as executor:
        processed = 0
        with bounded_as_completed(
            executor,
            jar_files,
            submit_scan,
            max_in_flight=max(1, workers * 2),
        ) as completed:
            for future, jar_path in completed:
                details = future.result()
                content = details.content
                # 跳過沒有匹配檔案且可能為 bad zip 的 JAR（bad zip 會 log warning 並回傳 {}）
                # 若 JAR 有内容則一定會有至少一筆記錄（即使是 None 的 binary 檔）
                if content:  # 空 dict 表示沒有任何匹配，或 bad zip 被跳過
                    results[jar_path] = content
                    results.entries[jar_path] = details.entries
                results.nested_jars[jar_path] = details.nested_jars
                results.skipped_nested[jar_path] = details.skipped_nested
                if details.collision_notes:
                    results.collision_notes[jar_path] = details.collision_notes
                processed += 1
                if processed_callback:
                    processed_callback(processed, total)

    return results
