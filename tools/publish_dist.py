"""把打包輸出（staging）安全地發佈到正式執行資料夾，不刪除任何使用者資料。

用法（由 tools/build_exe.bat 呼叫）：
    python tools/publish_dist.py --staging dist/_staging/main.dist \\
        --target dist/MinecraftTranslator --exe MinecraftTranslator.exe \\
        --update-file config.example.json \\
        --seed-file config.example.json:config.json \\
        --seed-file replace_rules.json

契約（有 tests/test_publish_dist.py 保護）：
1. 只做「複製／覆蓋」，**永遠不刪除** target 內任何既有檔案或資料夾
   （config.json、logs/、快取資料/、學名資料庫/、.icon_cache/、輸出資料夾等）。
2. staging 不存在或缺少 exe 時直接失敗，且不動 target（build 失敗不破壞可用版本）。
3. ``--update-file``：每次覆蓋（隨程式附帶、使用者不編輯的檔案）。
4. ``--seed-file SRC[:DEST]``：只有 DEST 不存在時才複製（使用者會編輯的檔案，如
   config.json、replace_rules.json）。
5. 代價：舊版遺留、新版已不存在的程式檔不會被清掉（無害，但不會自動變小）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


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
    # dirs_exist_ok：合併進既有資料夾；只會寫入 staging 內有的檔案，不會刪除 target 任何東西
    shutil.copytree(staging, target, dirs_exist_ok=True)

    for src, dest in update_files or []:
        shutil.copy2(src, target / dest)
    for src, dest in seed_files or []:
        dest_path = target / dest
        if not dest_path.exists():
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_path)


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
