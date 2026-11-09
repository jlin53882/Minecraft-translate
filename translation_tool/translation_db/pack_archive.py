"""從單一 ZIP 抽出 KubeJS／FTB Quests 來源，讓既有的模組包掃描可以直接讀取。

模組包掃描（:func:`pack_sources.scan_pack_sources`）以磁碟目錄為單位。翻譯 ZIP 若是
整個模組包的壓縮檔（內含 ``kubejs``、``config/ftbquests/quests``），這裡只把掃描會讀的
檔案（``.js``／``.json``／``.snbt``）寫到暫存目錄，之後沿用同一套探索與解析，不另寫
第二套 ZIP 解析器。

安全邊界：路徑逸出、絕對路徑、符號連結一律略過並回報；每個成員與整個 ZIP 都有解壓縮
上限（:class:`ZipReadBudget`），超過就停止並保留已抽出的部分。暫存目錄由呼叫端管理。
"""

from __future__ import annotations

import hashlib
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from translation_tool.utils.zip_safety import (
    MAX_TEXT_BYTES,
    ArchiveBudgetError,
    ZipReadBudget,
    ZipSizeError,
    read_limited,
)

PACK_SUFFIXES = frozenset({".js", ".json", ".snbt"})
_MAX_WRAPPER_DEPTH = 4


def zip_pack_scope(zip_path: str | Path) -> str:
    """ZIP 的模組包身分：只依 ZIP 檔本身決定，不受暫存目錄影響，重複匯入才會對到同一批來源。"""
    resolved = Path(zip_path).expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    return f"{resolved.name}@{digest}"


def _is_pack_source_member(parts: tuple[str, ...]) -> bool:
    """成員是否位在 ``kubejs`` 或 ``config/ftbquests/quests`` 之下（允許外層 wrapper 目錄）。"""
    lowered = tuple(part.lower() for part in parts[:-1])
    for index in range(min(len(lowered), _MAX_WRAPPER_DEPTH + 1)):
        if lowered[index] == "kubejs":
            return True
        if lowered[index : index + 3] == ("config", "ftbquests", "quests"):
            return True
    return False


def _safe_parts(name: str) -> tuple[str, ...] | None:
    """回傳可安全接在目的目錄下的路徑片段；絕對路徑、``..``、磁碟機代號回 ``None``。"""
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or not path.parts:
        return None
    for part in path.parts:
        if part in {"", ".", ".."} or ":" in part:
            return None
    return path.parts


def _issue(archive: Path, member: str, reason: str, detail: str) -> dict[str, str]:
    return {
        "source_archive": str(archive),
        "source_member": member,
        "kind": "pack_source",
        "status": "pending",
        "reason": reason,
        "detail": detail,
    }


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def extract_pack_members(
    zip_path: str | Path,
    dest: str | Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> list[dict[str, str]]:
    """把 ZIP 內的模組包來源檔抽到 ``dest``（保留含 wrapper 的完整相對路徑）。

    回傳問題清單（不拋出）：壞掉的 ZIP、不安全的成員、超過解壓縮預算都以問題記錄，
    已抽出的檔案仍留在 ``dest``。
    """
    archive = Path(zip_path)
    root = Path(dest).resolve()
    issues: list[dict[str, str]] = []
    try:
        zf = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        return [_issue(archive, "", "archive_read_failed", str(exc))]
    budget = ZipReadBudget.for_pack(archive.name)
    with zf:
        for info in zf.infolist():
            if should_cancel is not None and should_cancel():
                break
            if info.is_dir() or Path(info.filename).suffix.lower() not in PACK_SUFFIXES:
                continue
            parts = _safe_parts(info.filename)
            if parts is None:
                issues.append(
                    _issue(
                        archive, info.filename, "archive_member_unsafe", "路徑不安全"
                    )
                )
                continue
            if not _is_pack_source_member(parts):
                continue
            if _is_symlink(info):
                issues.append(
                    _issue(
                        archive,
                        info.filename,
                        "source_link_skipped",
                        "符號連結不會被抽出",
                    )
                )
                continue
            target = root.joinpath(*parts)
            if root not in target.resolve().parents:
                issues.append(
                    _issue(archive, info.filename, "archive_member_unsafe", "路徑逸出")
                )
                continue
            try:
                data = read_limited(zf, info, MAX_TEXT_BYTES, budget)
            except ZipSizeError as exc:
                issues.append(
                    _issue(archive, info.filename, "source_too_large", str(exc))
                )
                continue
            except ArchiveBudgetError as exc:
                issues.append(
                    _issue(archive, info.filename, "archive_budget", str(exc))
                )
                break
            except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                issues.append(
                    _issue(archive, info.filename, "archive_read_failed", str(exc))
                )
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return issues
