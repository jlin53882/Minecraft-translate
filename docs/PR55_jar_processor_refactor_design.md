# PR #55 設計文件：jar_processor_extract 重構（複用 jar_browser.py）

> 狀態：規劃中
> 前提：PR #52（jar_browser.py）已合併，PR #53 已合併

---

## 1. 動機與目標

### 1.1 現況問題

`jar_processor_extract.py` 目前有自己的 JAR 掃描邏輯：

```python
# jar_processor_extract.py 現有程式碼
with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
    future_to_jar = {
        executor.submit(extract_from_jar_fn, jar, output_dir, target_regex): jar
        for jar in jar_files
    }
```

這段邏輯和 `icon_preview_view`（PR #53）即將有的多執行緒掃描**高度重複**。

### 1.2 目標

- `jar_processor_extract` 改用 `jar_browser.scan_jars()`
- 消除重複的 JAR 掃描邏輯
- 所有 JAR 讀取統一走 `jar_browser`

---

## 2. 現有邏輯分析

### 2.1 jar_processor_extract 的工作流程

```
1. 收集要提取的 JAR 檔案（from config 或自動發現）
2. 對每個 JAR：
   a. 用 ThreadPoolExecutor 多執行緒執行
   b. extract_from_jar_fn(jar, output_dir, target_regex)
      - 開啟 ZIP
      - 用 regex 找目標檔案（預設：lang/en_us.json）
      - 複製到 output 目錄
3. 進度回呼（每個 JAR 完成後呼叫一次）
```

### 2.2 與 jar_browser 的差異

| 項目 | jar_processor_extract | jar_browser（PR #52）|
|------|---------------------|---------------------|
| 目的 | 提取檔案到磁碟 | 讀取內容到記憶體 |
| 回傳 | 無（直接寫檔）| `dict[JAR, dict[path, content]]` |
| 執行緒管理 | 自己的 ThreadPoolExecutor | jar_browser 內部管理 |
| 錯誤處理 | extract_from_jar_fn 內部 | _scan_single_jar 內部 |

---

## 3. 重構方案

### 3.1 兩種整合方式

**方式 A（推薦）：只把 ThreadPoolExecutor 替換成 jar_browser**

```python
# 舊：自己的 ThreadPoolExecutor
with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
    future_to_jar = {
        executor.submit(extract_from_jar_fn, jar, output_dir, target_regex): jar
        for jar in jar_files
    }
    for future in concurrent.futures.as_completed(future_to_jar):
        ...

# 新：用 jar_browser 讀取，用自己的方式寫入磁碟
results = scan_jars(
    jar_dir=jar_dir,
    patterns=[target_regex],  # 動態傳入
    processed_callback=processed_callback,
)

# 用 jar_browser 的結果寫入磁碟
for jar_path, files in results.items():
    for file_path, content in files.items():
        output_path = output_dir / jar_path.stem / file_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content.encode() if isinstance(content, str) else content)
```

**方式 A 的優點**：
- 保留原有的檔案寫入邏輯（extract_from_jar_fn 的寫檔機制很成熟）
- 只替換 JAR 讀取層，風險最低
- processed_callback 仍然由 jar_processor_extract 控制

### 3.2 實作變更

```python
# extract_from_jar_fn 的新實作（保留檔案複製邏輯）：
def extract_from_jar_to_disk(
    jar_path: Path,
    output_dir: Path,
    target_pattern: str,
) -> dict[str, Any]:
    """從 JAR 讀取符合 pattern 的檔案，寫入磁碟。"""
    from translation_tool.utils.jar_browser import scan_jars

    results = scan_jars(
        jar_dir=jar_path.parent,
        patterns=[target_pattern],
        max_workers=1,  # 單一 JAR，max_workers 無意義
    )

    jar_results = results.get(jar_path, {})
    written = 0
    for file_path, content in jar_results.items():
        output_path = output_dir / jar_path.stem / file_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content.encode() if isinstance(content, str) else content)
        written += 1

    return {"jar": jar_path.name, "written": written}
```

**注意**：`extract_from_jar_to_disk` 現在變成接收「整個 JAR 目錄」而非單一 JAR。這是 API 變更，需要確保向後相容。

---

## 4. API 相容性考量

### 4.1 現有 API 簽名

```python
# 現有（必須維持向後相容）：
def extract_jars(
    jar_dir: Path,
    output_dir: Path,
    target_regex: str = r"lang/en_us\.json",
    max_workers: int | None = None,
    processed_callback=None,
) -> dict[str, Any]:
```

### 4.2 維持向後相容

`jar_browser.scan_jars` 接收 `jar_dir`（目錄），和現有 API 相容。包裝層不需改變外部簽名，只需要內部實作替換。

---

## 5. 單元測試

### 5.1 測試策略

**核心原則**：重構不應改變輸出結果。測試要驗證「重構前」和「重構後」的輸出完全相同。

```python
def test_extract_result_same_as_before(tmp_path):
    """驗證重構前後輸出相同"""
    jar_dir = create_test_jars(tmp_path)
    output_dir = tmp_path / "output"
    target_regex = r"lang/en_us\.json"

    # 重構前輸出
    result_before = extract_jars(jar_dir, output_dir, target_regex, max_workers=2)

    # 清除輸出
    shutil.rmtree(output_dir)

    # 重構後輸出
    result_after = extract_jars(jar_dir, output_dir, target_regex, max_workers=2)

    assert result_before["total_jars"] == result_after["total_jars"]
    assert result_before["total_files"] == result_after["total_files"]
```

### 5.2 測試案例

| 測試名 | 說明 |
|--------|------|
| `test_extract_uses_jar_browser` | 確認呼叫了 jar_browser（使用 mock）|
| `test_extract_parallel_matches_config` | max_workers 從 config 正確讀取 |
| `test_extract_result_same_as_before` | 重構前後輸出相同 |
| `test_extract_bad_jar_ignored` | 壞的 JAR 不影響整體 |
| `test_extract_progress_callback` | 每個 JAR 完成後 callback 被呼叫 |

### 5.3 測試檔位置

```
tests/test_jar_processor_jar_browser_integration.py
```

---

## 6. 檔案變更

| 檔案 | 變更類型 |
|------|---------|
| `translation_tool/core/jar_processor_extract.py` | 修改（內部實作重構）|
| `tests/test_jar_processor_jar_browser_integration.py` | 新增 |

---

## 7. 依賴變更

| 模組 | 變更 |
|------|------|
| `translation_tool.utils.jar_browser` | 新增依賴 |
| `concurrent.futures` | 仍然需要（用於非 JAR 的其他並行處理）|
