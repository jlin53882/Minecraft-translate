# PR30 設計稿（草案）：unused functions cleanup + 工作樹雜訊收斂

> 狀態：Design Draft（待家豪確認）
> 範圍：只做低風險清理，不動 UI 行為與頁面結構

---

## Summary
本 PR 先處理兩件低風險事項：
1. 移除 5 個目前僅剩「定義本身」的舊函數（全 repo 無呼叫點）
2. 處理工作樹雜訊（`.tmp/`、`.pytest-tmp/`）並補 `.gitignore` 規則，避免未來反覆出現

---

## 為什麼先做這顆
- 風險低：不改 UI、不改流程分支、不改核心演算法行為
- 回收快：先清掉明確 dead code，降低後續重構噪音
- 可驗證：用 `rg` + pytest 即可快速確認無回歸

---

## Scope（本 PR 會做）

### A) 移除 5 個無引用函數
1. `translation_tool/core/lm_translator_main.py`
   - 移除：`safe_json_loads_old`
2. `translation_tool/utils/text_processor.py`
   - 移除：`safe_convert_text_old`
3. `translation_tool/utils/cache_manager.py`
   - 移除：`get_cache_size_old`
4. `translation_tool/core/lm_translator.py`
   - 移除：`build_minimal_dict`
   - 移除：`group_by_file`

> 盤點依據：每個符號以 `rg` 全 repo 搜尋，皆只命中自身定義（count=1）。

### B) 工作樹雜訊處理
1. `.gitignore` 新增：
   - `.pytest-tmp/`
   - `.tmp/`
2. 清理本次產生的暫存目錄：
   - `.pytest-tmp/`
   - `.tmp/`

---

## Out of scope（明確不做）
- 不調整任何 UI 頁面（含 `cache_view.py`、`qc_view.py`）
- 不做 `translation_tool/core/*` 巨檔結構重拆
- 不做 cache/search 架構重構
- 不變更 `app.services` / QC 策略

---

## 影響面與風險
- **功能風險：低**（僅刪除無引用函數）
- **測試風險：低**（現有測試可覆蓋 import 與主要流程）
- **維運風險：低**（僅新增 ignore 規則 + 刪暫存目錄）

潛在風險：
- 若 repo 外部腳本硬引用這 5 個舊函數，會出現 ImportError
- 緩解：本 PR 先以 repo 內呼叫點為準；若你有外部腳本依賴，先列名單再補 shim

---

## 刪除/移除說明（本 PR）

### 刪除項目：5 個舊函數
- **為什麼改**：符號在 repo 內無任何使用，屬於 dead code，會增加閱讀負擔。
- **為什麼能刪**：全 repo 搜尋只命中函數定義本身（count=1）。
- **目前誰在用 / 沒人在用**：repo 內無使用點；repo 外部使用情況未知（待使用者確認）。
- **替代路徑是什麼**：
  - `safe_json_loads_old` -> `safe_json_loads`
  - `safe_convert_text_old` -> `safe_convert_text`
  - 其餘 3 個為純殘留 helper，無替代需求
- **風險是什麼**：外部私有腳本若有引用，會失敗。
- **如何驗證**：`rg` 無引用 + pytest 全量通過。

### 移除項目：工作樹暫存雜訊
- **為什麼改**：避免每次測試後出現不必要未追蹤檔，干擾 PR 檢查。
- **為什麼能刪**：皆為 pytest/uv 暫存輸出，不屬於原始碼或配置。
- **目前誰在用 / 沒人在用**：僅本地執行期間暫時使用。
- **替代路徑是什麼**：保留生成機制，但由 `.gitignore` 屏蔽。
- **風險是什麼**：幾乎無；下次執行會自動重建。
- **如何驗證**：`git status --short` 無 `.tmp/` / `.pytest-tmp/` 未追蹤噪音。

---

## Validation checklist
- [ ] `rg -n --glob "*.py" --glob "!backups/**" "\bsafe_json_loads_old\b|\bsafe_convert_text_old\b|\bget_cache_size_old\b|\bbuild_minimal_dict\b|\bgroup_by_file\b" .`
- [ ] `git status --short`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr30 -o cache_dir=.pytest-cache\pr30`

---

## Rejected approaches
1) 試過：直接進入 UI 大檔（如 `cache_view.py`）同步重構。  
2) 為什麼放棄：你已明確要求本輪 UI 先不動，且風險與驗證成本會暴增。  
3) 最終改採：先做低風險 dead code + 工作樹雜訊清理，再決定下一階段。

---

## Next step（待你確認後）
1. 依此設計稿完成 Phase 2 驗證（跑 checklist）
2. 更新本檔為「已實作/已驗證」版本
3. commit + push + 回報 PR30 結果
