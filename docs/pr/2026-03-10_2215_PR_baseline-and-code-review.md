# PR Title
chore: establish refactor baseline and ship code review bundle v4

# PR Description

## Summary
這個 PR 不做功能修改，也不進行正式重構。
目的是先把 `Minecraft_translator_flet` 的重構前基線建立好，包含：

- 建立專案程式索引 `INDEX.md`
- 產出並修正多輪分析報告（最終為 v4）
- 整理出全域 canonical 重構順序
- 先跑測試建立 baseline，避免後續重構時無法判斷是「本來就壞」還是「重構弄壞」

---

## What was done

### 1. 建立分析索引
新增 / 整理：
- `.agentlens/INDEX.md`

內容包含：
- 專案 Python 檔案清單
- 每個檔案的一句功能摘要

---

### 2. 產出並整理分析報告
建立 / 修正以下報告：

- `01-main-py-review.md`
- `02-app-review.md`
- `03-translation-tool-review.md`
- `code-review.md`

並多次依照稽核結果修正，最終整理為 **v4 bundle**。

---

### 3. 修正報告內部問題
v4 已修正以下問題：

- `01-main-py-review.md` 重複章節標題
- `code-review.md` 與 `03-translation-tool-review.md` 對 `cache_search.py` / `cache_index.py` 的矛盾
- 補上 tests baseline 作為重構前置條件
- 補上根目錄 maintenance / test 腳本的存在與待處理狀態
- 將 `cache_manger` 明確標記為 naming debt
- 將 `code-review.md` 定義為唯一的全域 canonical 重構順序

---

### 4. 建立測試 baseline
已執行：

```bash
uv run pytest
```

目前 baseline 結果為：

- **pytest 在 collection 階段失敗**
- 發現 **3 個既有 import 錯誤**

錯誤如下：

- `tests/test_cache_controller.py`
  - `ModuleNotFoundError: No module named 'app.views.cache_controller'`
- `tests/test_cache_presenter.py`
  - `ModuleNotFoundError: No module named 'app.views.cache_presenter'`
- `tests/test_cache_view_state_gate.py`
  - `ModuleNotFoundError: No module named 'app.views.cache_controller'`

這代表目前 test suite 還不能當作綠燈 baseline，但已成功確認：
> 這些錯誤是 **重構前就存在的既有問題**

---

## Important findings

### High priority before refactor
1. 先保留這次測試結果作為 baseline
2. 後續重構前，先決定是否要修：
   - `app.views.cache_controller`
   - `app.views.cache_presenter`
   的 import 路徑問題
3. `cache_search.py` 已存在，後續不得再另起一個平行的 `cache_index.py`

---

## Not included in this PR
這個 PR **沒有做** 以下事情：

- 沒有修改 Flet UI 結構
- 沒有開始拆 `services.py`
- 沒有開始拆 `lm_translator_main.py`
- 沒有開始拆 `lang_merger.py`
- 沒有正式刪除 old helpers
- 沒有 rename `cache_manger/`

---

## Next step
建議下一個 PR 順序：

### PR 2
低風險清理：
- old helpers 確認與刪除
- `_last_log_flush` 清理
- `preview_jar_extraction_service()` 壞引用修正 / 移除
- `main.py` 雜訊註解清理

### PR 3
邊界整理：
- `config_manager.py` import side effect 移除
- `main.py` 啟動責任收斂
- `app/services.py` 先做分層準備

---

## Test result
```text
uv run pytest
Result: failed during collection
Known pre-existing errors: 3
```
