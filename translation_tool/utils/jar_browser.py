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

import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_warning, log_error


def _get_default_workers() -> int:
    """從 config 讀取 max_workers，若無設定則 fallback 為 CPU 核心數的一半。

    回傳：
        int: 最大執行緒數（至少 1）
    """
    try:
        config = load_config()
        config_workers = config.get("translator", {}).get("parallel_execution_workers")
        if isinstance(config_workers, int) and config_workers > 0:
            return config_workers
    except Exception:
        # config 讀取失敗時不 blocking，直接用 fallback
        pass
    return max(1, os.cpu_count() // 2)


def _scan_single_jar(
    jar_path: Path,
    patterns: list[str],
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
    result: dict[str, str | None] = {}
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            for name in zf.namelist():
                for pattern in patterns:
                    if re.search(pattern, name):
                        try:
                            result[name] = zf.read(name).decode("utf-8")
                        except UnicodeDecodeError:
                            result[name] = None
                        break
    except zipfile.BadZipFile:
        log_warning("[jar_browser] BadZipFile: %s", jar_path.name)
    except Exception as ex:
        log_error("[jar_browser] Failed %s: %s", jar_path.name, ex)
    return jar_path, result


def scan_jars(
    jar_dir: Path,
    patterns: list[str],
    max_workers: int | None = None,
    processed_callback: Callable[[int, int], None] | None = None,
) -> dict[Path, dict[str, str | None]]:
    """平行讀取多個 JAR 內符合 pattern 的檔案內容。

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
    # 找出所有 JAR 檔案
    jar_files = list(jar_dir.glob("*.jar")) if jar_dir.is_dir() else []
    total = len(jar_files)

    # 決定 worker 數量
    workers = max_workers if max_workers is not None else _get_default_workers()

    results: dict[Path, dict[str, str | None]] = {}

    # 空目錄或無 JAR 檔：直接回傳空 dict
    if not jar_files:
        return results

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_jar = {
            executor.submit(_scan_single_jar, jar_path, patterns): jar_path
            for jar_path in jar_files
        }

        processed = 0
        for future in as_completed(future_to_jar):
            jar_path, content = future.result()
            # 跳過沒有匹配檔案且可能為 bad zip 的 JAR（bad zip 會 log warning 並回傳 {}）
            # 若 JAR 有内容則一定會有至少一筆記錄（即使是 None 的 binary 檔）
            if content:  # 空 dict 表示沒有任何匹配，或 bad zip 被跳過
                results[jar_path] = content
            processed += 1
            if processed_callback:
                processed_callback(processed, total)

    return results