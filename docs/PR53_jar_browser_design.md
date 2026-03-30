# PR #53 設計文件：jar_browser.py（共用的多執行緒 JAR 讀取工具）

> 狀態：規劃中
> 目的：提供一個共用的多執行緒 JAR 讀取工具，供 icon_preview_view 和 jar_processor_extract 複用

---

## 1. 動機與目標

### 1.1 現況問題

- `icon_preview_view` 的 `_load_entries_from_jar_directory()` 是同步 for 迴圈
- `jar_processor_extract.py` 也有自己的 JAR 掃描邏輯，兩者重複
- 未來任何需要讀取 JAR 的功能都會面臨同樣的重複問題

### 1.2 目標

- 建立一個共用的 `jar_browser.py` 工具模組
- 封裝多執行緒 + 錯誤隔離 + 進度回呼
- 供所有需要讀取 JAR 的功能複用

---

## 2. 設計決策

### 2.1 為什麼用 ThreadPoolExecutor（而非 ProcessPoolExecutor）

| 考量 | ThreadPoolExecutor | ProcessPoolExecutor |
|------|-------------------|-------------------|
| 任務類型 | I/O bound（磁碟讀取） | CPU bound（大量計算）|
| GIL 行為 | I/O 時自動釋放，不受限 | 完全不受 GIL 影響 |
| 序列化 | 共享記憶體，無需序列化 | 需要 pickle IPC |
| 適用性 | ✅ JAR 讀取正好是 I/O bound | ❌ 過度設計 |

JAR 讀取的瓶頸在磁碟 I/O，不是 CPU。`zipfile.read()` 和 `json.loads()` 都是 C 層執行，I/O 時 GIL 自動釋放，所以多執行緒完全够用。

### 2.2 為什麼不直接用 asyncio

asyncio 需要全鏈路 async，從 `zipfile.open()` 到 callback 都要改。代價極高，效益不成比例。

### 2.3 錯誤隔離設計

每個 JAR 的處理都被 `try/except` 包住，單一 JAR 失敗不影響整體結果：
```python
try:
    with zipfile.ZipFile(jar_path, 'r') as zf:
        ...
except zipfile.BadZipFile:
    log_warning(f"JAR 無效，跳過: {jar_path.name}")
    continue
except Exception as ex:
    log_error(f"JAR 讀取失敗: {jar_path.name} - {ex}")
    continue
```

---

## 3. API 設計

### 3.1 核心函式

```python
# 位置：translation_tool/utils/jar_browser.py

from pathlib import Path
from typing import Callable

def scan_jars(
    jar_dir: Path,
    patterns: list[str],
    max_workers: int | None = None,
    processed_callback: Callable[[int, int], None] | None = None,
) -> dict[Path, dict[str, str]]:
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

    回傳：
        {
            jar_path: {
                "assets/modid/lang/en_us.json": "{...json content...}",
                "icon.png": "<binary content or path>",
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
```

### 3.2 Pattern 擴充設計

目前需求只需一個 pattern，但設計預留多 pattern 支援：

```python
# 目前：只需要讀 en_us.json
patterns = [r"assets/([^/]+)/lang/en_us\.json"]

# 未來擴充（只需加 pattern，不需改核心）：
patterns = [
    r"assets/([^/]+)/lang/en_us\.json",   # 翻譯
    r"fabric\.mod\.json",                  # Fabric icon 路徑
    r"neoforge\.mods\.toml",               # NeoForge logoFile
]
```

### 3.3 max_workers 來源

```python
from translation_tool.core.config_loader import load_config

def _get_default_workers() -> int:
    config = load_config()
    config_workers = config.get("translator", {}).get("parallel_execution_workers")
    if isinstance(config_workers, int) and config_workers > 0:
        return config_workers
    import os
    return max(1, (os.cpu_count() or 2) // 2)  # 預設：CPU 核心數的一半
```

---

## 4. 內部實作

### 4.1 工作函式（純函式，無共享狀態）

```python
def _scan_single_jar(
    jar_path: Path,
    patterns: list[str],
) -> tuple[Path, dict[str, str]]:
    """掃描單一 JAR，符合 pattern 的檔案內容讀取出來。

    這是一個純函式：相同輸入永遠產生相同輸出，無副作用。
    這是 ThreadPoolExecutor 的最佳實踐。
    """
    result: dict[str, str] = {}
    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            for name in zf.namelist():
                for pattern in patterns:
                    if re.search(pattern, name):
                        try:
                            try:
                            result[name] = zf.read(name).decode("utf-8")
                        except UnicodeDecodeError:
                            # Binary 檔案（如 .png）：不解碼，設為 None 表示 caller 自行處理
                            result[name] = None
                        break  # 一個檔案只讀一次
    except zipfile.BadZipFile:
        log_warning(f"[jar_browser] 不是有效的 ZIP/JAR: {jar_path.name}")
    except Exception as ex:
        log_error(f"[jar_browser] 讀取失敗: {jar_path.name} - {ex}")
    return jar_path, result
```

### 4.2 ThreadPoolExecutor 主體

```python
import concurrent.futures

def scan_jars(...) -> dict[Path, dict[str, str]]:
    jar_files = list(jar_dir.glob("*.jar"))
    total = len(jar_files)
    workers = max_workers or _get_default_workers()

    results: dict[Path, dict[str, str]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_jar = {
            executor.submit(_scan_single_jar, jar_path, patterns): jar_path
            for jar_path in jar_files
        }

        processed = 0
        for future in concurrent.futures.as_completed(future_to_jar):
            jar_path, content = future.result()
            results[jar_path] = content
            processed += 1
            if processed_callback:
                processed_callback(processed, total)

    return results
```

### 4.3 進度回呼時機

- 每個 JAR 完成後呼叫一次
- 在 `concurrent.futures.as_completed()` 迴圈內更新

---

## 5. icon_preview_view 的整合方式

### 5.1 取代現有程式碼

```python
# 舊程式碼（PR #51）：
def _load_entries_from_jar_directory(self, processed_callback=None):
    jar_files = list(self.source_root.glob("*.jar"))
    for jar_path in jar_files:  # 同步迴圈
        with zipfile.ZipFile(jar_path) as zf:
            ...

# 新程式碼（PR #53）：
def _load_entries_from_jar_directory(self, processed_callback=None):
    results = scan_jars(
        jar_dir=self.source_root,
        patterns=[r"assets/([^/]+)/lang/en_us\.json"],
        processed_callback=processed_callback,
    )
    for jar_path, files in results.items():
        en_us = files.get(...)  # 直接取內容
```

### 5.2 icon 掃描支援（PR #53）

```python
results = scan_jars(
    jar_dir=self.source_root,
    patterns=[
        r"assets/([^/]+)/lang/en_us\.json",
        r"fabric\.mod\.json",
        r"neoforge\.mods\.toml",
    ],
    processed_callback=processed_callback,
)

for jar_path, files in results.items():
    en_us_data = files.get(...)  # 翻譯內容
    fabric_meta = files.get("fabric.mod.json")  # Fabric icon
    neoforge_meta = files.get("neoforge.mods.toml")  # NeoForge logoFile
```

---

## 6. 單元測試

### 6.1 測試環境

使用 Python 標準庫 `zipfile` 建立測試用 JAR 檔案：
```python
import zipfile, pathlib, tempfile

def create_test_jar(tmp_dir, jar_name, files):
    jar_path = tmp_dir / jar_name
    with zipfile.ZipFile(jar_path, 'w') as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return jar_path
```

### 6.2 測試案例

| 測試名 | 說明 | 預期行為 |
|--------|------|---------|
| `test_scan_jars_basic` | 一個 JAR，一個 pattern | 回傳正確內容 |
| `test_scan_jars_multiple_patterns` | 多個 pattern | 每個 pattern 的內容都讀到 |
| `test_scan_jars_bad_zip_ignored` | 其中一個 JAR 是壞的 | 其他 JAR 正常處理，壞的跳過 |
| `test_scan_jars_exception_isolated` | 其他 Exception | 不影響整體 |
| `test_scan_jars_callback_called` | 有 callback | 每個 JAR 完成後 callback 被呼叫一次 |
| `test_scan_jars_returns_dict` | 回傳型別 | `dict[Path, dict[str, str]]` |
| `test_scan_jars_empty_dir` | 空目錄 | 回傳空 dict |
| `test_scan_jars_no_jar_files` | 沒有 JAR 檔 | 回傳空 dict |
| `test_scan_jars_workers_from_config` | max_workers=None | 從 config 讀取 |
| `test_scan_jars_workers_default` | max_workers=None, config 沒有設定 | 用 CPU count 預設值 |

### 6.3 測試檔位置

```
tests/test_jar_browser.py
```

---

## 7. 檔案變更

| 檔案 | 變更類型 |
|------|---------|
| `translation_tool/utils/jar_browser.py` | 新增 |
| `tests/test_jar_browser.py` | 新增 |

---

## 8. 依賴

| 模組 | 用途 |
|------|------|
| `zipfile` | 讀取 JAR 內容 |
| `concurrent.futures` | ThreadPoolExecutor |
| `translation_tool.utils.config_manager` | 讀取 max_workers |
| `translation_tool.utils.log_unit` | log_info / log_warning / log_error |

> ⚠️ 注意：binary 檔案（如 `.png`）解碼時不應使用 `latin-1`（會產生乱码）。`scan_jars` 預設只處理文字檔（`.json`、`.toml` 等），binary 檔案的路徑在結果中以 `None` 表示，由 caller 自行處理。

---

## 9. 擴充性承諾

- **Pattern 可任意擴充**：只需在 `patterns` 清單新增正則，不需改核心
- **新 JAR 類型支援**：只需在 `_scan_single_jar` 新增對應的 pattern
- **Binary 檔案回傳 `None`**：binary 檔案（如 `.png`）嘗試 UTF-8 decode 失敗後設為 `None`，由 caller 自行處理（如讀取原始 binary 或跳過）
