# PR34 設計稿：non-UI guard tests 擴充（重構前安全網）

## Summary
在 PR35~PR37 進場前，先補齊 non-UI guard tests，讓核心重構有可依賴的回歸判準。

---

## Phase 0 盤點（必做）
- [ ] 盤點目前測試覆蓋缺口（plugins shared / logging bootstrap / core entry）
- [ ] 列出 PR35~37 將變動的模組與對應 guard test
- [ ] 明確定義「最小可接受回歸範圍」

---

## Phase 1 設計範圍（具體測試項）
### A. PR31/PR32 對應測試
1. `plugins/shared/json_io.py`
   - 讀取非 dict JSON 時應拋錯
   - 寫入後可回讀且內容一致
2. `plugins/shared/lang_path_rules.py`
   - `en_us.json -> zh_tw.json` 規則
   - 路徑含語系資料夾時替換正確
3. `plugins/shared/lang_text_rules.py`
   - `_strip_fmt` 對格式碼處理一致
   - `is_already_zh` 對中英文樣本判定一致

### B. PR33 對應測試
4. logger bootstrap 時機
   - pipeline 第一個可觀測步驟前，handler 已綁定
   - 多次呼叫不重複掛載 handler（若設計要求 idempotent）

### C. PR35/PR36 對應 smoke
5. `lm_translator_main` 關鍵入口 smoke
6. `lang_merger` 關鍵入口 smoke

---

## Out of scope
- 不做核心重構
- 不改 UI

---

## 刪除/移除/替換說明
- 本 PR 以新增/補強測試為主；若移除測試 fixture，需逐項說明原因與替代測試。

---

## Validation checklist
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr34 -o cache_dir=.pytest-cache\pr34`
- [ ] `uv run pytest -q tests/test_cache_search_orchestration.py tests/test_path_resolution.py --basetemp=.pytest-tmp\pr34-focus -o cache_dir=.pytest-cache\pr34-focus`
- [ ] `git diff --name-only`（確認主要是 tests + docs）

---

## Rejected approaches
1) **方案**：不補測試，直接進 PR35~PR37。  
   **放棄原因**：無法準確判定重構回歸。  
2) **方案**：只補 smoke test，不補行為測試。  
   **放棄原因**：邊界條件仍無保護。  
3) **最終採用**：先補具體 guard tests，再動核心大檔。

---

## Next
PR35：`lm_translator_main.py` 第一階段模組切分。
