# PR39C：維護性註解補齊（非 test 檔）

## Summary
本 PR 為 `Minecraft_translator_flet` 專案中 **所有非 test 的 `.py` 檔案** 補上維護性註解。

本 PR **不改邏輯、不改行為、不補 test**，只補：
- 模組 docstring（檔頭說明）
- class docstring
- 函數 docstring
- 複雜流程段落維護註解

---

## Phase 0 盤點

### 範圍
- `translation_tool/**/*.py`（排除 `tests/`）
- `app/**/*.py`（排除 `tests/`）
- `main.py`

### 不包含
- `tests/**/*.py`
- `backups/**/*.py`
- `.venv/**/*.py`
- `__pycache__/**/*.py`

### 目前基線
- 部分模組已有 docstring（如 `cache_manager.py`、`cache_store.py`）
- 部分函數已有簡短註解，但不一致
- 大檔（如 `lm_translator.py`、`kubejs_translator.py`）函數密集，但缺乏統一維護說明

---

## Phase 1 設計範圍

### 1. 模組 docstring
每個 `.py` 檔頂部補：
- 模組定位
- 主要責任
- 關鍵設計原則
- 維護注意

### 2. Class docstring
每個 class 補：
- 用途
- 主要屬性說明
- 主要方法契約
- 使用情境

### 3. 函數 docstring
每個函數補：
- 用途（一句話）
- 參數說明
- 回傳值說明
- 副作用（若有）
- 維護注意（若有）

### 4. 複雜流程段落
對以下類型補行內維護註解：
- cache state / live-reference 契約
- UI / session glue
- compatibility layer
- retry / batch policy
- path resolution
- logging / progress hook

---

## Out of scope
- 不改邏輯
- 不改 import 行為
- 不補 test
- 不順手重構
- 不刪舊碼（除非明顯 dead code）

---

## 刪除/移除/替換說明
- **無**（本 PR 只做註解補齊，不刪不改）

---

## Validation checklist
- [ ] `uv run python -c "import translation_tool; import app; import main; print('import-smoke-ok')"`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr39c -o cache_dir=.pytest-cache\pr39c`
- [ ] 確認所有非 test `.py` 檔都有模組 docstring
- [ ] 確認所有公開函數都有 docstring

---

## Rejected approaches
1. **方案**：只補大檔，小檔跳過。  
   **放棄原因**：會造成不一致，維護成本更高。  
2. **方案**：一次補完 + 順手重構。  
   **放棄原因**：混兩種事，風險太高。  
3. **最終採用**：只補註解，不改邏輯，全專案非 test 檔一致處理。

---

## 執行順序
1. 先備份（`backups/pr39c-...`）
2. 補 `translation_tool/` 核心層
3. 補 `app/` 應用層
4. 補 `main.py`
5. import smoke
6. full pytest
7. 更新 PR 文件

---

## Test result
待執行
