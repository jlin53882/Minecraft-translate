"""把打包輸出（staging）發佈成「app/ 與 data/ 分離」的正式資料夾。

用法（由 tools/build_exe.bat 呼叫）：
    python tools/publish_dist.py --staging dist/_staging/main.dist \\
        --target dist/MinecraftTranslator --exe MinecraftTranslator.exe \\
        --launcher MinecraftTranslator.bat \\
        --update-file config.example.json \\
        --seed-file config.example.json:config.json \\
        --seed-file replace_rules.json

輸出結構：

    <target>/
     ├─ MinecraftTranslator.bat   ← 啟動器（每次覆蓋）
     ├─ app/                      ← 程式：每次整個換掉（含 --update-file）
     └─ data/                     ← 使用者資料：永遠不刪除、不覆蓋（只由 --seed-file 補首次檔案）

契約（有 tests/test_publish_dist.py 保護）：
1. ``app/`` 完全取代：新版沒有的舊 DLL / PYD / 套件不會殘留，結果等價於乾淨 build。
2. ``data/`` 從不被刪除或覆蓋。``--seed-file SRC[:DEST]`` 只在 ``data/DEST`` 不存在時才建立。
3. staging 不存在、缺 exe、來源檔案缺漏，都在動 target 之前失敗（build 失敗不破壞可用版本）。
4. 替換流程：先複製到 ``app.new``，再 ``app → app.old``、``app.new → app``，最後刪 ``app.old``。
   任何一步失敗都會還原 ``app``；程式正在執行（檔案被鎖）時會失敗並提示先關閉。
5. 舊版平面式安裝（exe 與資料混在 target 根目錄）：不刪任何東西；使用者資料會在新版
   第一次啟動時由程式搬進 ``data/``（見 translation_tool/utils/app_paths.py）。
   舊的程式檔留在根目錄，確認新版正常後可手動刪除。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

APP_DIR = "app"
DATA_DIR = "data"
_NEW = "app.new"
_OLD = "app.old"


class PublishError(RuntimeError):
    """staging 不完整等無法安全發佈的情況。"""


def _validate(
    staging: Path,
    exe_name: str,
    update_files: list[tuple[Path, str]],
    seed_files: list[tuple[Path, str]],
) -> None:
    if not staging.is_dir():
        raise PublishError(f"staging 不存在：{staging}")
    if not (staging / exe_name).is_file():
        raise PublishError(f"staging 缺少 {exe_name}，build 可能失敗：{staging}")
    for src, dest in [*update_files, *seed_files]:
        if not Path(src).is_file():
            raise PublishError(f"來源檔案不存在：{src}")
        dest_path = Path(dest)
        if dest_path.is_absolute() or ".." in dest_path.parts:
            raise PublishError(f"目的路徑必須是相對路徑且不得含 ..：{dest}")


def publish(
    staging: Path,
    target: Path,
    exe_name: str,
    *,
    update_files: list[tuple[Path, str]] | None = None,
    seed_files: list[tuple[Path, str]] | None = None,
    launcher_name: str | None = None,
) -> None:
    staging = Path(staging)
    target = Path(target)
    update_files = list(update_files or [])
    seed_files = list(seed_files or [])
    _validate(staging, exe_name, update_files, seed_files)

    target.mkdir(parents=True, exist_ok=True)
    app_dir = target / APP_DIR
    app_new = target / _NEW
    app_old = target / _OLD

    # 1. 先在旁邊準備好完整的新 app（失敗時正式 app 完全沒被動過）
    for leftover in (app_new, app_old):
        if leftover.exists():
            shutil.rmtree(leftover)  # 這兩個名稱只屬於本腳本，上次中斷的殘留
    try:
        shutil.copytree(staging, app_new)
        for src, dest in update_files:
            dest_path = app_new / dest
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_path)
    except OSError as exc:
        shutil.rmtree(app_new, ignore_errors=True)
        raise PublishError(f"複製新版程式失敗：{exc}") from exc

    # 2. 換上新版；任何一步失敗都還原
    _swap(app_dir, app_new, app_old)

    # 3. 使用者資料：只補首次檔案，永不覆蓋
    data_dir = target / DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    for src, dest in seed_files:
        dest_path = data_dir / dest
        if not dest_path.exists():
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_path)

    # 4. 啟動器（屬於程式端，每次覆蓋）
    if launcher_name:
        (target / launcher_name).write_text(
            f'@echo off\r\nstart "" "%~dp0{APP_DIR}\\{exe_name}"\r\n',
            encoding="utf-8",
        )


def _swap(app_dir: Path, app_new: Path, app_old: Path) -> None:
    had_old = app_dir.exists()
    try:
        if had_old:
            app_dir.rename(app_old)
        app_new.rename(app_dir)
    except OSError as exc:
        # 還原：新版放回 app.new，舊版放回 app
        if had_old and app_old.exists() and not app_dir.exists():
            try:
                app_old.rename(app_dir)
            except OSError:
                raise PublishError(
                    f"換上新版失敗且無法還原，舊版在 {app_old}：{exc}"
                ) from exc
        shutil.rmtree(app_new, ignore_errors=True)
        raise PublishError(
            f"無法替換 {app_dir}（程式可能正在執行，請先關閉後重試）：{exc}"
        ) from exc
    if had_old:
        # 新版已就位；舊版刪不掉（例如被防毒掃描中）只是殘留，不影響使用
        shutil.rmtree(app_old, ignore_errors=True)


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
    parser.add_argument("--launcher")
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
            launcher_name=args.launcher,
        )
    except PublishError as exc:
        print(f"發佈失敗（原有的 app/ 與 data/ 未被修改）：{exc}", file=sys.stderr)
        return 1
    print(f"已發佈到 {args.target}（app/ 已更新，data/ 未被動到）")
    if (args.target / args.exe).exists():
        print(
            f"注意：{args.target} 根目錄仍有舊版平面式的 {args.exe}。"
            "使用者資料會在新版第一次啟動時搬進 data/；確認正常後可手動刪除舊程式檔。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
