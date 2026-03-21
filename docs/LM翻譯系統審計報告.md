# LM 翻譯系統審計報告

**日期**：2026-03-21
**審計範圍**：`translation_tool/core/lm_*.py`
**目標**：找出執行流程問題與改善建議

---

## 一、執行流程總覽

```
translate_directory_generator (lm_translator.py)
  │
  ├─ scan_translatable_files()          # 掃描可翻譯 JSON
  ├─ extract_items_parallel()           # 並行抽取文字 items
  ├─ Cache 命中分流                    # 已有快取 → 直接寫入
  └─ while remaining:                   # 主翻譯循環
       translate_batch_smart()          # 呼叫翻譯
         ├─ _validate_batch_items()
         ├─ _detect_batch_profile()     # 從未被呼叫（死碼）
         ├─ _calculate_batch_size()     # 從未被呼叫（死碼）
         ├─ _execute_translation()
         └─ translate_batch_smart_old() # 實際執行的函式
              ├─ 迴圈: model pool
              │   ├─ call_gemini_requests()   # API 呼叫
              │   ├─ safe_json_loads()        # 解析回應
              │   ├─ 漏翻檢查
              │   └─ 截斷偵測
              ├─ 503 過載重試邏輯
              ├─ 429 限流處理
              └─ 批次動態縮小
```

---

## 二、發現的問題（分級）

### 🔴 嚴重（影響正確性）

#### 1. 新架構 `dry_run` 和 `export_cache_only` 完全無效

**檔案**：`lm_translator_main.py`

`translate_batch_smart()` 接收了 `dry_run` 和 `export_cache_only` 參數，但 `_execute_translation()` 直接忽略它們：

```python
# 新架構
def _execute_translation(items, batch_size, batch_profile, total,
                         dry_run=False, export_cache_only=False):
    # dry_run 和 export_cache_only 完全被忽略
    return translate_batch_smart_old(items, total)  # ← 沒傳參數！
```

**後果**：`translate_batch_smart()` 無法真正執行「只輸出快取」或「模擬翻譯」。

**修復方向**：將參數傳遞到 `translate_batch_smart_old()` 或重構新架構讓 `dry_run` 在進入翻譯迴圈前攔截。

---

#### 2. Config key 拼寫錯誤，導致設定值永遠讀不到

**檔案**：`config.json`

```json
"iniital_batch_size_patchouli": 200   // ← 拼錯：iniital 不是 initial
"iniital_batch_size_lang": 200        // ← 拼錯
```

**受影響的程式碼**（所有 `lm_translator_main.py` 和 `lm_translator_shared_loop.py` 中的 `iniital_batch_size_*`）：
- `lm_translator.py` 中的 fallback 值：`iniital_batch_size_lang` → 永遠使用 `300`（設定檔是 `200`）
- `lm_translator_main.py`：`iniital_batch_size_patchouli` → 永遠使用 `100`（設定檔是 `200`）
- `lm_translator_shared_loop.py`：同樣問題

**後果**：Batch size 設定值完全無法生效，實際使用的都是 Python 程式碼中的 fallback 值。

**修復方向**：
1. 將 `config.json` 中的 `iniital_batch_size_*` 改為 `initial_batch_size_*`（或維持拼錯但統一代碼）
2. 將程式碼中所有 `iniital_batch_size_*` 統一拼寫

---

#### 3. `INITIAL_BATCH_SIZE_LANG` 重複定義（第二次覆寫第一次）

**檔案**：`lm_translator_main.py` 的 `translate_batch_smart_old()` 內

```python
# 第一次定義（在 lm_translator.py 頂層，已有）
INITIAL_BATCH_SIZE_LANG = load_config().get("iniital_batch_size_lang", 300)

# translate_batch_smart_old() 內又定義兩次
INITIAL_BATCH_SIZE_LANG = (
    load_config().get("iniital_batch_size_lang", 300)
)
# ... 之後又定義一次（覆寫）
INITIAL_BATCH_SIZE_LANG = lm_cfg.get("iniital_batch_size_lang", 200)
```

**後果**：值最終是 `200`（因為最後一次定義），但整個函式內的變數可讀性差，容易出錯。

---

#### 4. `detect_batch_profile` 巢狀定義兩次（第二次是死碼）

**檔案**：`lm_translator_main.py` 的 `translate_batch_smart_old()` 內

```python
def translate_batch_smart_old(batch_items, total=None):
    def detect_batch_profile(items):  # ← 第一次定義
        ...

    ...

    # 第二次定義（舊版被註解覆蓋，新版直接覆寫）
    def detect_batch_profile(items):  # ← 第二次定義（覆寫第一次）
        # 包含更多 cache_type 判斷
        ...
```

**後果**：第一次定義的函式被第二次覆寫，浪費程式碼空間，且容易造成混淆。

---

### 🟡 中等（影響可維護性）

#### 5. 新架構的 `_detect_batch_profile` 和 `_calculate_batch_size` 完全未被呼叫

**檔案**：`lm_translator_main.py`

`translate_batch_smart()` 內有兩個輔助函式：
- `_detect_batch_profile()` → 從未被 `_execute_translation` 呼叫
- `_calculate_batch_size()` → 同上

**後果**：死碼堆積，維護者以為新架構會用到這些邏輯，實際上完全沒被使用。

---

#### 6. 程式碼中 `load_config()` 被重複呼叫

`lm_translator_main.py` 中的 `translate_batch_smart_old()` 呼叫 `load_config()` 至少 **3 次**（分別讀 `lm_translator`、`initial_batch_size_*`、`batch_shrink_factor`），而 `lm_translator.py` 的頂層已經呼叫過一次。

**修復方向**：在 `lm_translator.py` 頂層讀取一次，透過參數傳遞到翻譯函式。

---

#### 7. 每批次後固定等待 12 秒不合理

**檔案**：`lm_translator_main.py`

```python
if remaining_count == 0:
    log_info("⏳ 等待 12 秒以避免觸發 RPM 限制…")
    time.sleep(12)  # ← 無論有沒有限流都等待
```

**問題**：
- 當 API 沒有回傳 `Retry-After` 時浪費等待時間
- 只剩一個 batch 的時候才等待，但迴圈中每個成功的 batch 都已經花了時間

**修復方向**：
- 從 API 429 回應中解析 `RetryInfo.retryDelay` 並動態調整等待時間
- 批次間已間隔一段 API 呼叫時間，通常不需要額外等待

---

#### 8. ETA 計算了但從未顯示給使用者

**檔案**：`lm_translator.py`

```python
eta_seconds = int(len(remaining) * avg_per_item)
# ...
if (not is_interrupted and processed > 0):
    eta_text = f"預計剩餘: {format_duration_seconds(eta_seconds)}"
```

`eta_text` 變數在進度訊息中組裝了，但整個 `translate_directory_generator` 是 generator 模式，這段程式碼實際上被包含在 yield 的 dict 中。**實際上這段是有被執行的**（組進 progress_msg），但回顧 `lm_translator.py` 中的 yield，ETA 並未出現在 UI 顯示的 log 欄位中。

**建議**：明確透過 `yield {"log": progress_msg}` 將 ETA 傳遞給 UI 顯示。

---

#### 9. 翻譯後快取寫入頻率過高

**檔案**：`lm_translator.py`

每處理完一個 batch 就呼叫 `save_translation_cache()`，寫入硬碟一次：
```python
if is_lang:
    save_translation_cache("lang", write_new_shard=write_new_cache)
else:
    save_translation_cache("patchouli", write_new_shard=write_new_cache)
```

**問題**：每批次都寫檔（磁碟 I/O 瓶頸），且中斷後來不及優化。**但這個設計也是保護機制**，避免進度因 crash 全部丟失。

**建議**：改為「每 N 批次寫入一次」，加上「翻譯結束前統一寫入」的策略，而非每批都寫。

---

### 🟢 輕微（影響穩健性）

#### 10. JSON 截斷偵測方法不夠嚴謹

**檔案**：`lm_translator_main.py`

```python
if not raw_text.endswith(("}", "]")):
    # 認為是截斷，縮小 batch
```

**問題**：如果翻譯結果本身就是以其他字元結尾（例如 `null`），會被誤判為截斷。

**建議**：改用更嚴謹的偵測方法：
- 嘗試解析失敗才認定截斷
- 或計算大括號 `{}` 的平衡（合法 JSON 必須配對）

---

#### 11. 翻譯結果未翻品質驗證

目前只靠日誌輸出「疑似未翻」數量，沒有實際機制：
- 驗證翻譯結果與原文是否相同（lazy translation）
- 驗證翻譯長度是否合理（例如翻譯後長度為 0 或與原文完全相同）

**建議**：在翻譯後增加簡單的品質檢查：
- 翻譯後長度為 0 → 標記為失敗重翻
- 翻譯後與翻譯前相同，且原文含英文字母 → 標記為 lazy

---

#### 12. 缺乏斷點續傳機制

若翻譯過程因任何原因中斷，已翻譯的內容會寫入快取（checkpoint），但 `remaining_items` 列表需要從頭開始，無法從上次中斷處復原。

**建議**：將 `remaining_items` 的處理進度（已處理到哪個 index）寫入快取或獨立檔案，下次執行時從中斷點繼續。

---

#### 13. 測試覆蓋缺口

144 個測試中，缺少以下關鍵路徑的測試：
- **`translate_batch_smart_old` 的 503 過載重試流程**（沒有測試覆蓋）
- **Batch 縮小邏輯的邊界條件**（batch size = min_batch_size 時的行為）
- **JSON 截斷時的處理邏輯**
- **`call_gemini_requests` 的網路逾時場景**
- **翻譯結果品質低於預期的偵測**

---

## 三、Config 設定值衝突總整理

| 設定 Key | 位置 | 值 | 影響 |
|---------|------|-----|------|
| `iniital_batch_size_patchouli` | config.json | **200** | 永遠讀不到 |
| `initial_batch_size_patchouli` | config.json | **100** | 正確的 key，但值是 100 |
| `iniital_batch_size_lang` | config.json | **200** | 永遠讀不到 |
| `initial_batch_size_lang` | config.json | **300** | 正確的 key，但值是 300 |
| `min_batch_size` | config.json | **50** | 正常讀取 |
| `batch_shrink_factor` | config.json | **0.5** | 正常讀取 |

**結論**：存在兩組 key：`iniital_*`（錯字，200/200）和 `initial_*`（正確，100/300）。程式碼全部使用 `iniital_*`，所以設定檔的 `initial_*` 值完全沒用到。實際使用的值：
- Patchouli：設定檔 200，但程式 fallback 100
- Lang：設定檔 200，但程式 fallback 300

---

## 四、改善建議優先順序

| 順序 | 項目 | 等級 | 說明 |
|------|------|------|------|
| 1 | 修復 `iniital` 拼寫 | 🔴 嚴重 | 導致設定完全失效 |
| 2 | 修復 `dry_run`/`export_cache_only` 無效 | 🔴 嚴重 | 破壞新架構正確性 |
| 3 | 清除巢狀重複定義 | 🔴 嚴重 | 維護混淆風險 |
| 4 | 刪除死碼 `_detect_batch_profile` 等 | 🟡 中等 | 減少維護負擔 |
| 5 | 統一 `load_config()` 呼叫次數 | 🟡 中等 | 效能優化 |
| 6 | 增加 JSON 截斷偵測嚴謹度 | 🟢 輕微 | 避免誤判 |
| 7 | 增加翻譯品質驗證 | 🟢 輕微 | 提升輸出品質 |
| 8 | 增加斷點續傳 | 🟢 輕微 | UX 改善 |

---

## 五、結論

LM 翻譯系統的核心邏輯（API 呼叫 → 解析 → 批次縮小 → 錯誤處理）是完整且穩健的，但存在三個系統性問題：

1. **架構分裂**：新架構（`translate_batch_smart`）和舊實作（`translate_batch_smart_old`）混合，新架構的參數無法傳遞到實際執行的函式
2. **Config 拼寫錯誤**：導致設定值完全無法生效
3. **程式碼重複堆積**：巢狀函式重複定義、`load_config()` 多次呼叫，造成維護負擔

建議一次重構解決這三個核心問題，再逐步處理其他改善項目。
