# PR66 設計稿：Cache 效能優化

> 版本：v1.1（修復版）  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
利用 PR61 canonicalize 後的 cache 結構，優化 cache 命中率與讀寫效能。

### 動機
- PR61 完成後，cache import 已 canonicalize，結構更清晰
- 可基於清晰的結構進行效能優化
- 減少重複翻譯，提升使用者體驗

---

## 2. 現狀分析

### 2.1 Cache 架構（PR61 後）
- `translation_tool/utils/cache_store.py` - 狀態持有（Runtime state holder）
- `app/views/cache_manager/` - Cache manager façade
- `get_cache_dict_ref()` - 取得 live reference 的正式 API
- 測試數量：42 個 cache 相關測試

### 2.2 Phase 0 盤點（先決步驟）

**在進入效能優化前，必須先完成以下分析**：

1. **Cache Key 生成邏輯分析**
   - 找到 key 生成的程式碼位置
   - 分析是否精準（是否會產生 collision）

2. **讀寫路徑分析**
   - 確認何時寫入磁碟
   - 確認讀取時的處理流程
   - 找出可能的效能瓶頸

3. **Benchmark 基準建立**
   - 建立效能基準測試腳本
   - 記錄優化前的效能數據

**Phase 0 驗證指令**：
```bash
uv run pytest -k cache -v  # 確認 cache 測試通過
```

### 2.4 隱性風險與緩解措施

| 風險 | 嚴重度 | 緩解措施 |
|------|--------|----------|
| Cache key 變更導致舊 cache 失效 | 🔴 高 | 保持 key 生成邏輯向後相容，或提供 migration |
| Lazy write 資料遺失 | 🔴 高 | 搭配「定期寫入」或「checkpoint」機制 |
| PR61 依賴 | 🟡 中 | PR66 需在 PR62 完成後執行（確保測試穩定）|

### 2.5 潛在效能瓶頸（待 Phase 0 確認）
1. **Cache Key 設計**
   - 可能存在 collision（不同 input 產生相同 key）
   - key 生成邏輯可能不夠精準

2. **讀寫路徑**
   - 每次翻譯都要寫入磁碟？還是只在 session 結束時？
   - 讀取時是否有不必要的 json parse？

3. **命中率**
   - 相同內容是否會重複翻譯？
   - 大小寫/空白是否影響 key？

---

## 3. 預期改動

### 3.1 Phase 0：分析與盤點（先決步驟）

**必須先完成以下分析，才能進入 Phase 1**：

#### 3.1.1 Cache Key 分析
- 找到 key 生成的程式碼位置
- 分析是否精準（是否會產生 collision）
- 輸出 Phase 0 報告

#### 3.1.2 Benchmark 建立
- 建立效能基準測試腳本
- 記錄優化前的效能數據

#### 3.1.3 Phase 0 驗證
```bash
uv run pytest -k cache -v  # 確認 42 個 cache 測試通過
```

---

### 3.2 Phase 1：Cache Key 優化（基於 Phase 0 分析）

#### 3.2.1 實作改進
- 若 Phase 0 發現 collision 風險，改進 key 演算法
- 考慮加入 source text 的 hash 作為 key 一部分

### 3.3 Phase 2：讀寫路徑優化（基於 Phase 0 分析）

#### 3.3.1 可能的優化
- Lazy write（只在 session 結束時寫入）
- 記憶體 cache + 定期寫入策略
- 減少不必要的 json parse

### 3.4 Phase 3：Cache Warm-up（可選）

若時間允許，可加入：
- 啟動時預先載入常用 cache
- Background 定期寫入

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| Cache key 變更導致舊 cache 失效 | 🔴 高 | 保持向後相容，或提供 migration |
| Lazy write 資料遺失 | 🔴 高 | 搭配 checkpoint + 定期寫入 |
| 改動讀寫邏輯導致資料遺失 | 🔴 高 | 先備份 cache 檔案，測試 crash recovery |
| 效能優化反而變慢 | 🟡 中 | 保持原有邏輯做 benchmark |
| 影響翻譯正確性 | 🔴 高 | 完整測試覆蓋 |

---

## 4.1 PR 依賴關係

- **PR66 依賴 PR62**：需等 PR62 完成測試健檢後執行
- **原因**：PR62 確保測試穩定，PR66 改動風險高，需要穩定的測試基盤

---

## 5. Validation checklist

### Phase 0（先決）
- [ ] `uv run pytest -k cache -v` - 確認 cache 測試通過
- [ ] Phase 0 報告產出（Cache Key 分析 + Benchmark 建立 + 向後相容性評估）

### Phase 1
- [ ] 基於 Phase 0 分析的 Cache Key 優化
- [ ] **向後相容性驗證**：舊 cache 檔案仍可讀取
- [ ] `uv run pytest -k cache -v` - 確認 cache 測試通過

### Phase 2
- [ ] 基於 Phase 0 分析的讀寫路徑優化
- [ ] **資料遺失風險評估**：Crash test 模擬
- [ ] Benchmark 對比（優化後 vs 優化前）

### Phase 3
- [ ] `uv run pytest -q` - 確認所有測試通過

---

## 6. Rejected approaches

- 試過：直接改 cache key 生成邏輯（完全重寫）
- 為什麼放棄：**風險太高**，會導致所有舊 cache 失效，造成使用者大量重新翻譯
- 最終改採：在現有結構上做增量優化，保持向後相容

- 試過：每次翻譯後立即寫入磁碟
- 為什麼放棄：I/O 效能差
- 最終改採：Lazy write + 定期 checkpoint

- 試過：不做效能優化，直接進入 UI 重構
- 為什麼放棄：cache 是核心效能瓶頸，應該先處理
- 最終改採：先做安全性較高的優化（如 key 精準度檢查、讀寫路徑優化）

---

## 7. 隱性 BUG 檢查清單

- [ ] Cache key 變更是否破壞向後相容性？
- [ ] Lazy write 是否有資料遺失風險？
- [ ] Benchmark 是否能真實反映效能變化？
- [ ] Crash recovery 是否正常？

---

## 7. 刪除/移除/替換說明

本 PR 不刪除任何現有功能。若有優化，可能涉及：

### 可能的重構

| 項目 | 說明 | 風險 |
|------|------|------|
| Cache key 生成邏輯 | 改進 key 精準度 | 中（需確保向後相容） |
| 寫入策略 | 改變寫入時機 | 中（需確保資料不遺失） |

---

## 8. 預期產出

- Phase 1 完成清單（分析結果）
- Cache key 分析報告
- 優化後的 cache 邏輯（若有）
- Benchmark 腳本（可選）
- PR 文件：`docs/pr/2026-03-13_PR66_cache_performance.md`

---

## 9. 里程碑

| 階段 | 內容 |
|------|------|
| Phase 1 | 分析現有 cache key 與讀寫邏輯 |
| Phase 2 | 識別效能瓶頸 |
| Phase 3 | 實作優化（從低風險開始） |
| Phase 4 | Benchmark 驗證 |
| Phase 5 | 完整測試覆蓋 |

---

## 10. 重要提醒

- **安全優先**：任何 cache 改動都要確保資料不遺失
- **向後相容**：盡量保持與舊 cache 檔案的相容性
- **測試覆蓋**：改動前先確認測試完整
