# PR66 設計稿：Cache 監控版（PR66-A）

> 版本：v2.0（監控版）  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
建立 cache 監控與基準測量，不改變既有 cache 行為。

### 動機
- PR66 設計稿將 key 變更、lazy write 列為高風險
- 應該先取得數據，再決定是否優化
- 保持向後相容是最重要的

### 本次 PR66-A 範圍
- ✅ 新增 cache metrics（統計）
- ✅ 建立 benchmark 腳本
- ✅ 建立 collision observer（只記錄，不改行為）
- ❌ 不改 key 生成邏輯
- ❌ 不改寫入時機
- ❌ 不引入 lazy write

---

## 2. 現狀分析

### Cache 架構（PR61 後）
- `translation_tool/utils/cache_store.py` - 狀態持有
- `app/views/cache_manager/` - Cache manager façade
- Key 模式：`path` 或 `path|source_text`
- 測試數量：42 個 cache 相關測試

---

## 3. 預期改動

### 3.1 新增 Cache Metrics

在 `cache_store.py` 新增統計：
- `cache_load_ms` - 載入耗時
- `cache_save_ms` - 儲存耗時
- `cache_file_size_bytes` - 檔案大小
- `cache_hit_count` - 命中次數
- `cache_miss_count` - 未命中次數
- `cache_add_count` - 新增次數

### 3.2 建立 Benchmark 腳本

建立 `scripts/benchmark_cache.py`：
- 載入 benchmark
- 命中率 benchmark
- 儲存 benchmark

### 3.3 Collision Observer

只記錄，不改行為：
- 當 key 已存在但資料不一致時，記錄 warning
- 累計 collision_suspect_count

### 3.4 保持向後相容

- 不改 CacheRule.make_key()
- 不改 cache 檔格式
- 不改 add_to_cache() 語義
- 不改 save_translation_cache() 時機

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| Metrics 影響效能 | 🟡 低 | 使用高效計時，避免複雜計算 |
| 向後相容破壞 | 🟡 低 | 只新增，不修改既有邏輯 |

---

## 5. Validation checklist

- [ ] `uv run pytest -k cache -v` - 確認 cache 測試通過
- [ ] 新增 metrics 可輸出
- [ ] Benchmark 腳本可執行
- [ ] 舊 cache 檔可正常載入
- [ ] 不影響既有 UI

---

## 6. Rejected approaches

- 試過：直接改 key 生成邏輯
- 為什麼放棄：高風險，會破壞舊 cache
- 最終改採：只做監控，不改行為

- 試過：引入 lazy write
- 為什麼放棄：資料遺失風險高
- 最終改採：保持現有寫入時機

---

## 7. 下一步（條件式）

只有 PR66-A 證明有問題才做：

- **PR66-B**：Write-path 優化（batched save）
- **PR66-C**：Key/Versioning（collision 證據）
