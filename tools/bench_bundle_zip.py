"""打包 ZIP 效能量測腳本（#165 驗收：真實輸出目錄的前後數字）。

用途：對「真實的打包輸入目錄」量測 ZIP 階段的
      - level 9（舊）與 level 6（新）的耗時與大小
      - 來源不變時：強制重建 vs 沿用既有 ZIP 的耗時
      並驗證 level 6 與 level 9 解壓後內容逐 byte 相同。

安全：輸出一律寫到暫存目錄，不會動到來源目錄與你現有的 ZIP。

使用方式（在專案根目錄執行）：
    python tools/bench_bundle_zip.py <input_root_dir> [--runs 3] [--extra PATH ...]
    # Windows 例：
    .\\.venv\\Scripts\\python.exe tools\\bench_bundle_zip.py "C:\\path\\to\\bundle_input" --runs 3

      <input_root_dir> 就是打包頁「來源根目錄」(bundle_outputs_generator 的 input_root_dir)。
      --description / --extra 與正式打包相同的參數，可選。

輸出：終端表格 + 可直接貼進 PR 的 Markdown。--json PATH 另存原始數字。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translation_tool.core import output_bundler  # noqa: E402


def _scan(root: Path, extras: list[str]) -> tuple[int, int]:
    files = size = 0
    for base in [str(root), *extras]:
        if os.path.isfile(base):
            files, size = files + 1, size + os.path.getsize(base)
            continue
        for dirpath, _, names in os.walk(base):
            for name in names:
                files += 1
                size += os.path.getsize(os.path.join(dirpath, name))
    return files, size


def _bundle(
    root: Path, zip_path: Path, level: int, force: bool, kwargs: dict
) -> tuple[float, str]:
    """跑一次打包，回傳 (耗時秒, 最後一則 log)。"""
    output_bundler.ZIP_COMPRESS_LEVEL = level
    start = time.perf_counter()
    last: dict = {}
    for update in output_bundler.bundle_outputs_generator(
        str(root), str(zip_path), force_rebuild=force, **kwargs
    ):
        last = update
        if update.get("error"):
            raise RuntimeError(update.get("log", "打包失敗"))
    return time.perf_counter() - start, last.get("log", "")


def _median(values: list[float]) -> float:
    return statistics.median(values)


def _contents(zip_path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(zip_path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _mb(n: int) -> float:
    return n / 1024 / 1024


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input_root_dir", type=Path)
    ap.add_argument("--runs", type=int, default=3, help="每種情境重複次數，取中位數")
    ap.add_argument(
        "--extra", action="append", default=[], help="額外資料夾／檔案（可重複）"
    )
    ap.add_argument("--description", default="bench")
    ap.add_argument("--json", type=Path, help="另存原始數字")
    ap.add_argument(
        "--skip-verify",
        action="store_true",
        help="略過 level 6 vs 9 內容比對（大資料較慢）",
    )
    args = ap.parse_args()

    root = args.input_root_dir
    if not root.is_dir():
        print(f"找不到目錄：{root}", file=sys.stderr)
        return 2

    kwargs = {"description": args.description, "extra_folders": args.extra or None}
    files, src_bytes = _scan(root, args.extra)
    original_level = output_bundler.ZIP_COMPRESS_LEVEL
    result: dict = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "input_root_dir": str(root),
        "files": files,
        "source_mb": round(_mb(src_bytes), 2),
        "runs": args.runs,
    }

    print(f"來源：{root}\n檔案數：{files}　未壓縮大小：{_mb(src_bytes):.1f} MB")
    print(f"環境：{result['platform']} / Python {result['python']}\n")

    try:
        with tempfile.TemporaryDirectory(prefix="bench_bundle_zip_") as tmp:
            tmp_dir = Path(tmp)
            zips: dict[int, Path] = {}
            for level in (9, 6):
                zip_path = tmp_dir / f"level{level}.zip"
                times = []
                for i in range(args.runs):
                    elapsed, _ = _bundle(root, zip_path, level, True, kwargs)
                    times.append(elapsed)
                    print(f"  level {level} 第 {i + 1}/{args.runs} 次：{elapsed:.2f} s")
                zips[level] = zip_path
                result[f"level{level}"] = {
                    "seconds": round(_median(times), 2),
                    "all_seconds": [round(t, 2) for t in times],
                    "zip_mb": round(_mb(zip_path.stat().st_size), 2),
                }

            # 以 level 6（目前預設）量測：來源不變時「強制重建」與「沿用」
            zip_path = zips[6]
            _bundle(root, zip_path, 6, True, kwargs)  # 建立有效狀態
            rebuild = []
            for _ in range(args.runs):
                elapsed, _ = _bundle(root, zip_path, 6, True, kwargs)
                rebuild.append(elapsed)
            reuse, reuse_ok = [], True
            for _ in range(args.runs):
                elapsed, log = _bundle(root, zip_path, 6, False, kwargs)
                reuse.append(elapsed)
                reuse_ok &= "沿用" in log
            result["unchanged_rebuild_seconds"] = round(_median(rebuild), 2)
            result["unchanged_reuse_seconds"] = round(_median(reuse), 3)
            result["reuse_hit"] = reuse_ok

            if args.skip_verify:
                result["contents_identical"] = None
            else:
                print("  比對 level 6 與 level 9 解壓後內容 ...")
                result["contents_identical"] = _contents(zips[6]) == _contents(zips[9])
    finally:
        output_bundler.ZIP_COMPRESS_LEVEL = original_level

    l9, l6 = result["level9"], result["level6"]
    time_delta = (l6["seconds"] - l9["seconds"]) / l9["seconds"] * 100
    size_delta = (l6["zip_mb"] - l9["zip_mb"]) / l9["zip_mb"] * 100
    speedup = l9["seconds"] / l6["seconds"] if l6["seconds"] else float("inf")
    reuse_speedup = (
        result["unchanged_rebuild_seconds"] / result["unchanged_reuse_seconds"]
        if result["unchanged_reuse_seconds"]
        else 0.0
    )

    md = f"""
### ZIP 打包量測（真實輸出目錄）
- 環境：{result["platform"]} / Python {result["python"]}
- 輸入：{files} 個檔案，未壓縮 {result["source_mb"]} MB（每項取 {args.runs} 次中位數）

| 情境 | 耗時 | ZIP 大小 |
|---|---|---|
| level 9（舊） | {l9["seconds"]:.2f} s | {l9["zip_mb"]:.2f} MB |
| level 6（新） | {l6["seconds"]:.2f} s | {l6["zip_mb"]:.2f} MB |
| 差異 | {time_delta:+.0f}%（快 {speedup:.1f}x） | {size_delta:+.1f}% |
| 來源不變：強制重建（level 6） | {result["unchanged_rebuild_seconds"]:.2f} s | — |
| 來源不變：沿用既有 ZIP | {result["unchanged_reuse_seconds"]:.3f} s（快 {reuse_speedup:.0f}x） | — |

- level 6 與 level 9 解壓後內容逐 byte 相同：{result["contents_identical"]}
- 沿用路徑確實命中：{result["reuse_hit"]}
"""
    print(md)
    if args.json:
        args.json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"原始數字已存到 {args.json}")
    return 0 if result["contents_identical"] is not False and result["reuse_hit"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
