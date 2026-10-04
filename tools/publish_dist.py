"""把打包輸出（staging）安全地發佈到正式執行資料夾：換掉程式檔、保留使用者資料。

用法（由 tools/build_exe.bat 呼叫）：
    python tools/publish_dist.py --staging dist/_staging/main.dist \\
        --target dist/MinecraftTranslator --exe MinecraftTranslator.exe \\
        --update-file config.example.json \\
        --seed-file config.example.json:config.json \\
        --seed-file replace_rules.json

檔案分兩類（有 tests/test_publish_dist.py 保護）：

A. **packaged files**（exe、DLL / PYD、Nuitka 相依樹、assets、隨附範本）
   新版應完全取代舊版。每次發佈會寫入 ``.packaged_manifest.json``，記錄「這次發佈了哪些
   packaged 檔案」；下次發佈時，**上一份 manifest 有、新版 staging 沒有**的檔案會被刪除，
   所以已移除的 DLL / PYD / 套件不會殘留成混合版本。

B. **使用者資料**（config.json、replace_rules.json、logs/、快取資料/、學名資料庫/、
   .icon_cache/、各輸出資料夾…）
   從不進入 manifest，所以永遠不會被刪除。另有一份保護清單作為第二道防線：即使 manifest
   被改壞，也不會刪除保護清單內的項目，或 target 之外的路徑。

其他規則：
1. staging 不存在或缺少 exe 時直接失敗，且不動 target（build 失敗不破壞可用版本）。
2. ``--update-file``：每次覆蓋，視為 packaged files（隨程式附帶、使用者不編輯）。
3. ``--seed-file SRC[:DEST]``：只有 DEST 不存在時才複製，視為使用者資料（不進 manifest）。
4. 已知限制：第一次使用本腳本時沒有舊 manifest，**無法判斷**舊版遺留的檔案是否屬於
   packaged files，所以不會刪除它們；從沒有 manifest 的舊安裝升級時，若要清乾淨需手動處理
   一次（之後的發佈就會自動維護）。
5. 流程是「先複製新檔，再刪除過期檔，最後寫 manifest」。中途失敗時重新執行即可（冪等）；
   它不是整個資料夾的原子替換。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

MANIFEST_NAME = ".packaged_manifest.json"
# 第二道防線：這些頂層項目永遠不會被刪除（使用者資料，或隨時可能被使用者編輯）
PROTECTED_TOP_LEVEL = frozenset(
    {
        "config.json",
        "replace_rules.json",
        "logs",
        "快取資料",
        "學名資料庫",
        ".icon_cache",
        MANIFEST_NAME,
    }
)


class PublishError(RuntimeError):
    """staging 不完整等無法安全發佈的情況。"""


def publish(
    staging: Path,
    target: Path,
    exe_name: str,
    *,
    update_files: list[tuple[Path, str]] | None = None,
    seed_files: list[tuple[Path, str]] | None = None,
) -> None:
    staging = Path(staging)
    target = Path(target)
    if not staging.is_dir():
        raise PublishError(f"staging 不存在：{staging}")
    if not (staging / exe_name).is_file():
        raise PublishError(f"staging 缺少 {exe_name}，build 可能失敗：{staging}")
    # 先驗證所有來源檔案，避免發佈到一半才發現缺檔
    for src, _dest in [*(update_files or []), *(seed_files or [])]:
        if not Path(src).is_file():
            raise PublishError(f"來源檔案不存在：{src}")

    target.mkdir(parents=True, exist_ok=True)
    old_manifest = _read_manifest(target)

    # 1. 先複製新檔（只會寫入 staging 內有的檔案，不刪除任何東西）
    shutil.copytree(staging, target, dirs_exist_ok=True)
    packaged = {
        p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()
    }
    for src, dest in update_files or []:
        shutil.copy2(src, target / dest)
        packaged.add(Path(dest).as_posix())

    # 2. 刪除「上次發佈過、這次已不存在」的 packaged 檔案（不碰使用者資料）
    _remove_stale_packaged(target, old_manifest - packaged)

    # 3. 使用者資料：只在不存在時建立
    for src, dest in seed_files or []:
        dest_path = target / dest
        if not dest_path.exists():
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_path)

    # 4. 最後才寫 manifest（seed 檔案是使用者資料，不列入）
    packaged -= {Path(dest).as_posix() for _src, dest in seed_files or []} - {
        Path(dest).as_posix() for _src, dest in update_files or []
    }
    (target / MANIFEST_NAME).write_text(
        json.dumps(
            {"version": 1, "files": sorted(packaged)}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )


def _read_manifest(target: Path) -> set[str]:
    path = target / MANIFEST_NAME
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        files = data.get("files", [])
        return {f for f in files if isinstance(f, str)}
    except (OSError, ValueError, AttributeError):
        # manifest 壞掉：寧可不刪任何東西
        return set()


def _is_safe_to_delete(target: Path, rel: str) -> Path | None:
    """回傳可刪除的絕對路徑；不安全（越界、受保護、非一般檔案）則回傳 None。"""
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts or not rel_path.parts:
        return None
    if rel_path.parts[0] in PROTECTED_TOP_LEVEL:
        return None
    candidate = target / rel_path
    try:
        candidate.resolve().relative_to(target.resolve())
    except ValueError:
        return None
    if candidate.is_symlink() or not candidate.is_file():
        return None
    return candidate


def _remove_stale_packaged(target: Path, stale: set[str]) -> None:
    for rel in sorted(stale):
        path = _is_safe_to_delete(target, rel)
        if path is None:
            continue
        path.unlink()
        # 只修剪因此變空的資料夾；內含其他檔案（例如使用者資料）的資料夾不會被動
        parent = path.parent
        while parent != target and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


def _parse_pair(value: str) -> tuple[Path, str]:
    # Windows 路徑含 "C:"，所以只在最後一個 ":" 後面看起來像檔名時才當作 DEST
    if ":" in value:
        src, _, dest = value.rpartition(":")
        if src and dest and "\\" not in dest and "/" not in dest:
            return Path(src), dest
    return Path(value), Path(value).name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--staging", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--update-file", action="append", default=[])
    parser.add_argument("--seed-file", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        publish(
            args.staging,
            args.target,
            args.exe,
            update_files=[_parse_pair(v) for v in args.update_file],
            seed_files=[_parse_pair(v) for v in args.seed_file],
        )
    except PublishError as exc:
        print(f"發佈失敗（正式資料夾未被修改）：{exc}", file=sys.stderr)
        return 1
    print(f"已發佈到 {args.target}（未刪除任何既有資料）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
