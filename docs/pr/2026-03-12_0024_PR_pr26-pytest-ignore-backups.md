# PR Title
fix: ignore backups during pytest collection

# PR Description

## Summary
PR26 修正 `uv run pytest -q` 會被 `backups/` 污染測試收集的問題。
做法是把 pytest 收集設定補進 `pyproject.toml`，明確限制正式測試路徑為 `tests`，並排除 `backups` 目錄。

---

## Phase 1 完成清單
- [x] 做了：先重現 `uv run pytest -q` 失敗情況
- [x] 做了：確認 `backups/` 內確實存在完整 `tests/` 副本
- [x] 做了：確認專案目前沒有 pytest 收集設定
- [x] 做了：修改前先建立 `pyproject.toml` 備份
- [x] 做了：在 `pyproject.toml` 補上 pytest 收集設定
- [x] 做了：重新驗證 `uv run pytest -q` 恢復正常
- [x] 做了：重新驗證 `uv run pytest -q tests` 仍正常

---

## 刪除/移除/替換說明（若有，固定放這裡）

### 替換項目：`pyproject.toml` 新增 `[tool.pytest.ini_options]`
- **為什麼改**：目前直接跑 `uv run pytest -q` 會遞迴收集到 `backups/` 裡的舊測試副本，造成大量 `import file mismatch`。
- **為什麼能改**：這不是功能程式碼邏輯問題，而是 pytest 收集範圍沒有邊界；用 pytest 官方設定限制收集範圍是正確修法。
- **目前誰在用 / 沒人在用**：目前所有人直接跑 `uv run pytest -q` 都會受影響；`uv run pytest -q tests` 則本來就正常。
- **替代路徑是什麼**：不再依賴使用者手動記得加 `tests` 參數，改由 pytest 設定自動限制：
  - `testpaths = ["tests"]`
  - `norecursedirs = ["backups"]`
- **風險是什麼**：如果未來正式測試不放在 `tests/`，pytest 不會自動收進來；但以目前專案結構來看，這是正確且低風險的設定。
- **我是怎麼驗證的**：實跑 `uv run pytest -q` 與 `uv run pytest -q tests`，兩者都通過。

---

## What was done

### 1. 重現問題
先直接執行：
- `uv run pytest -q`

結果失敗，錯誤型態為大量：
- `import file mismatch`

### 2. 確認根因
實際盤點後確認：
- `backups/` 內存在完整 `tests/` 副本
- 專案目前沒有 `pytest.ini` / `pyproject.toml` 的 pytest 收集設定
- pytest 會把 `backups/` 和主專案 `tests/` 一起收進來，導致同名測試模組衝突

### 3. 修法
在 `pyproject.toml` 補上：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
norecursedirs = ["backups"]
```

這樣做的效果：
- `uv run pytest -q` 只收正式 `tests/`
- `backups/` 不再污染收集
- 不需要改動既有備份規則

### 4. 備份位置
本次修改前已建立可回退備份：
- `backups/pr26-pytest-backups-ignore-20260312-0022/pyproject.toml`

---

## Important findings
- 問題不在 PR17~PR25 的程式邏輯，而是 pytest 收集範圍沒有邊界。
- 根因是 repo 內保留了 `backups/`，而且其中包含完整 `tests/` 副本。
- 正確修法是調整 pytest 收集設定，不是破壞既有備份規則。

---

## Validation checklist
- [x] `uv run pytest -q`
- [x] `uv run pytest -q tests`

---

## Test result
```text
$ uv run pytest -q
.....................................                                    [100%]
37 passed in 1.36s

$ uv run pytest -q tests
.....................................                                    [100%]
37 passed in 1.34s
```
