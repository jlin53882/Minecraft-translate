"""受安全限制的巢狀 ZIP/JAR 遞迴走訪工具。

一般翻譯流程與 Mod DB 都會遇到把資源包在 ``META-INF/jarjar`` 或
``META-INF/jars`` 的模組。這個 helper 只負責 archive tree 的生命週期與安全
邊界；資源的分類與輸出位置由呼叫端決定。
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from translation_tool.utils.cancellation import raise_if_cancelled
from translation_tool.utils.zip_safety import (
    MAX_FILE_BYTES,
    ArchiveBudgetError,
    ZipReadBudget,
    ZipSizeError,
    read_limited,
)

NESTED_ARCHIVE_DIRS = ("META-INF/jarjar/", "META-INF/jars/")
MAX_NESTED_ARCHIVE_DEPTH = 3

ArchiveBudget = ZipReadBudget
ArchiveVisit = Callable[
    [zipfile.ZipFile, str, int, tuple[str, ...], ArchiveBudget], bool | None
]
NestedErrorHandler = Callable[[str, str, BaseException], None]
NestedNamePredicate = Callable[[str], bool]
NestedTruncationHandler = Callable[[str, int], None]


def _is_allowed_nested_member(
    info: zipfile.ZipInfo, nested_name_predicate: NestedNamePredicate | None
) -> bool:
    if info.is_dir():
        return False
    if nested_name_predicate is not None:
        return nested_name_predicate(info.filename)
    return is_nested_archive_name(info.filename)


def _report_nested_truncation(
    current: zipfile.ZipFile,
    current_label: str,
    depth: int,
    nested_name_predicate: NestedNamePredicate | None,
    on_nested_truncated: NestedTruncationHandler | None,
) -> None:
    if on_nested_truncated is None:
        return
    if any(
        _is_allowed_nested_member(info, nested_name_predicate)
        for info in current.infolist()
    ):
        on_nested_truncated(current_label, depth)


def is_nested_archive_name(name: str) -> bool:
    """回傳名稱是否是一般模組允許遞迴的內嵌 JAR。"""
    normalized = name.replace("\\", "/").lower()
    return normalized.endswith(".jar") and normalized.startswith(
        tuple(prefix.lower() for prefix in NESTED_ARCHIVE_DIRS)
    )


def walk_nested_archives(
    zf: zipfile.ZipFile,
    label: str,
    budget: ArchiveBudget,
    visit: ArchiveVisit,
    *,
    budget_factory: Callable[[str], ArchiveBudget] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    max_depth: int = MAX_NESTED_ARCHIVE_DEPTH,
    on_nested_error: NestedErrorHandler | None = None,
    on_nested_truncated: NestedTruncationHandler | None = None,
    archive_max_bytes: int | None = None,
    nested_name_predicate: NestedNamePredicate | None = None,
    scan_nested: bool = True,
) -> None:
    """以 bounded read 遞迴走訪外層 archive 與允許的內嵌 JAR。

    ``visit`` 會先收到目前 archive；回傳 ``False`` 時不再往該 archive 的
    下一層走訪。每一層可以用 ``budget_factory`` 取得自己的 archive budget，
    而外層讀取內嵌 JAR 的 bytes 仍使用父層 budget。這保留 Mod DB 原本的
    「每個 archive + 整棵 tree」安全模型；一般翻譯流程則可以傳入同一份
    budget，讓預掃描與抽取共用累計上限。

    損毀或過大的單一內嵌 JAR 會交給 ``on_nested_error`` 後略過；整棵 tree
    的 ``ArchiveBudgetError`` 會往外拋出，讓呼叫端把該 outer JAR 標記為失敗。
    """

    if archive_max_bytes is None:
        archive_max_bytes = MAX_FILE_BYTES

    if should_cancel is not None and should_cancel():
        raise_if_cancelled()

    if visit(zf, label, 0, (), budget) is False:
        return
    if not scan_nested:
        return

    def walk_children(
        current: zipfile.ZipFile,
        current_label: str,
        depth: int,
        archive_chain: tuple[str, ...],
        current_budget: ArchiveBudget,
    ) -> None:
        if depth >= max_depth:
            _report_nested_truncation(
                current,
                current_label,
                depth,
                nested_name_predicate,
                on_nested_truncated,
            )
            return
        for info in current.infolist():
            if not _is_allowed_nested_member(info, nested_name_predicate):
                continue
            if should_cancel is not None and should_cancel():
                raise_if_cancelled()
            name = info.filename.replace("\\", "/")
            nested_label = f"{current_label}!{name}"
            try:
                data = read_limited(
                    current,
                    info,
                    max_bytes=archive_max_bytes,
                    budget=current_budget,
                )
                with zipfile.ZipFile(io.BytesIO(data)) as nested:
                    child_budget = (
                        budget_factory(nested_label)
                        if budget_factory is not None
                        else current_budget
                    )
                    if (
                        visit(
                            nested,
                            nested_label,
                            depth + 1,
                            archive_chain + (name,),
                            child_budget,
                        )
                        is False
                    ):
                        continue
                    walk_children(
                        nested,
                        nested_label,
                        depth + 1,
                        archive_chain + (name,),
                        child_budget,
                    )
            except ArchiveBudgetError:
                raise
            except (zipfile.BadZipFile, ZipSizeError, OSError) as exc:
                if on_nested_error is not None:
                    on_nested_error(nested_label, name, exc)

    walk_children(zf, label, 0, (), budget)


def read_archive_member_chain(
    archive_path: str | Path,
    member_chain: tuple[str, ...],
    budget: ArchiveBudget,
    *,
    max_bytes: int = MAX_FILE_BYTES,
) -> bytes:
    """以同一套限制讀取 outer JAR 內某個 direct/nested 成員。"""
    if not member_chain:
        raise ValueError("member_chain 不可為空")

    with ExitStack() as stack:
        current = stack.enter_context(zipfile.ZipFile(archive_path, "r"))
        data = b""
        for index, member in enumerate(member_chain):
            data = read_limited(current, member, max_bytes=max_bytes, budget=budget)
            if index + 1 < len(member_chain):
                current = stack.enter_context(zipfile.ZipFile(io.BytesIO(data), "r"))
        return data


__all__ = [
    "MAX_NESTED_ARCHIVE_DEPTH",
    "NESTED_ARCHIVE_DIRS",
    "is_nested_archive_name",
    "read_archive_member_chain",
    "walk_nested_archives",
]
