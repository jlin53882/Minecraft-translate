# PR39C_2：修正 PR39C 自動產生的模板 docstring（維護註解修正版）

## Summary
PR39C 先把「非 test `.py` 檔的維護性 docstring」補齊，但其中有一批 docstring 是模板自動生成的占位內容（例如「用途說明／參數請見函式簽名／回傳內容依實作而定」），在實務上會造成誤導或完全沒資訊。

本 PR（PR39C_2）只做一件事：
- **把這批模板 docstring 重寫成「最小但不會誤導」的維護說明**

硬規則：
- 不改邏輯
- 不改行為
- 不重構
- 不補測試（本 PR 純註解修正）

---

## Phase 0 盤點

### 問題型態
典型模板內容：
- 「xxx 的用途說明」
- 「參數請見函式簽名」
- 「回傳內容依實作而定」

這類 docstring 無法讓維護者快速理解：
- 這個函式在專案裡的角色（service glue / core / util / UI）
- 是否為 generator（會 yield UI update dict）
- 主要包裝/呼叫哪個下層函式

### 掃描方式
- `rg -n "用途說明|參數請見函式簽名|回傳內容依實作而定" **/*.py --glob "!tests/**"`

### 影響範圍（本 PR 實際修正）
- patched files：64
- patched docstrings：445

---

## Phase 1 設計範圍

### 重寫原則（保守）
- 不推測細節：只寫「角色/用途/是否 generator/主要包裝呼叫」這種不容易寫錯的資訊
- docstring 保持繁體中文、且偏短
- 不在 docstring 內宣稱行為變更

### 新 docstring 格式（統一）
每個被修正的函式/方法 docstring 會包含：
- 函式名稱（`...`）
- 用途：一句話描述 +（若可判定）主要包裝/呼叫的函式名
- 參數：依函式簽名
- 回傳：
  - generator：明確寫「逐步 yield update dict」
  - 非 generator：None 或依 return path

---

## Out of scope
- 不改 import 行為
- 不調整型別註記
- 不調整 logger/log 文案
- 不新增/刪除任何檔案（除本 PR 文件本身）

---

## Validation checklist
- [ ] `uv run python -c "import translation_tool; import app; import main; print('import-smoke-ok')"`
- [ ] `mkdir -Force .pytest-tmp, .pytest-cache`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr39c_2 -o cache_dir=.pytest-cache\pr39c_2`

---

## Rejected approaches
1) 試過：人工逐檔逐函式手改 docstring。
   - 為什麼放棄：範圍太大、容易漏改且不一致；review 成本也過高。
   - 最終改採：用 AST 定位「模板 docstring」並一致性重寫（不動程式邏輯）。

2) 試過：用更激進的自動推導（依函式內容推斷 domain 行為）。
   - 為什麼放棄：推導容易寫錯，反而把錯誤資訊寫進註解。
   - 最終改採：只輸出保守、可被程式碼直接驗證的資訊（角色/是否 generator/主要 call）。

---

## Test result
- import smoke：`import-smoke-ok`
- pytest：`85 passed in 1.57s`
