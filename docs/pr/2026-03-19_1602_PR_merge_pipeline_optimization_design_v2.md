# 檔案合併管線（Merge）優化設計稿 v2｜可執行版

> 日期：2026-03-19 16:02
> 狀態：可交付他人執行
> 適用版本：minecraft-translator-flet（merge pipeline / view 設定同步）
> 目的：在不破壞既有流程的前提下，完成 merge 流程優化、Patchouli 判斷收斂、`zh_cn` 控制開關，以及 UI / View 端設定開關與語意說明補齊

---

## 一、最終範圍（本版定案）

本版不是只改後端 merge 判斷，**必須包含 View/UI 設定入口**，讓使用者可以在介面中直接控制行為。

### 本次必做
1. **P0 修正版**：Patchouli skip 判斷改為 Ratio 方案 1 + B-lite+
2. **P1 修正版**：`contains_cjk()` 僅對 `str` 分支加入 memoization
3. **P3 修正版**：ThreadPool worker 上限改為 `CPU // 2` capped version，並修正錯誤 config key
4. **新增 merge 設定欄位**：`zh_cn` 行為控制 + Patchouli skip 控制
5. **新增 View/UI 開關**：讓使用者可直接在介面切換設定
6. **補上按鈕/開關語意說明**：避免使用者誤解每個開關的實際效果

### 本次不做
1. JSON5 / comment fallback parser
2. SHA256 hash cache
3. 大型 UI 重構
4. 重新設計整個設定頁架構

---

## 二、最終設定規格

### 2.1 新增設定欄位
放在 `lang_merger` 區塊：

```json
{
  "lang_merger": {
    "process_zh_cn_files": true,
    "skip_zh_cn_when_only_process_lang": false,
    "patchouli_skip_en_us_when_zh_cn_exists": false,
    "patchouli_effective_translation_threshold": 0.5
  }
}
```

### 2.2 欄位定義

#### `process_zh_cn_files`
- 型別：`bool`
- 預設：`true`
- 語意：是否讓 `zh_cn` 一般情況下參與 merge 流程

#### `skip_zh_cn_when_only_process_lang`
- 型別：`bool`
- 預設：`false`
- 語意：當 `only_process_lang=true` 時，是否直接跳過 `zh_cn` lang 檔

#### `patchouli_skip_en_us_when_zh_cn_exists`
- 型別：`bool`
- 預設：`false`
- 語意：Patchouli 判斷中，是否允許 `zh_cn` 的有效翻譯結果影響 `en_us` skip

#### `patchouli_effective_translation_threshold`
- 型別：`float`
- 預設：`0.5`
- 語意：Patchouli 有效翻譯比例門檻（Ratio 方案 1 + B-lite+）

---

## 三、行為表（此版為準）

# 3.1 一般模式
`only_process_lang = false`

| 設定 | 行為 |
|------|------|
| `process_zh_cn_files = true` | `zh_cn` 正常參與 merge |
| `process_zh_cn_files = false` | `zh_cn` 全部跳過 |

---

# 3.2 只處理 lang 模式
`only_process_lang = true`

| 設定 | 行為 |
|------|------|
| `skip_zh_cn_when_only_process_lang = true` | `zh_cn` lang 檔直接跳過 |
| `skip_zh_cn_when_only_process_lang = false` | `zh_cn` lang 檔維持正常 |

> 注意：此規則優先於一般 merge 流程中的 `zh_cn` 處理邏輯

---

# 3.3 Patchouli `en_us` skip 模式

對某個 `book_root`：

- `zh_tw` 有效翻譯比例 >= threshold → 跳過 `en_us`
- `zh_cn` 有效翻譯比例 >= threshold，且 `patchouli_skip_en_us_when_zh_cn_exists = true` → 跳過 `en_us`
- 其他情況 → 不跳過 `en_us`

---

## 四、Patchouli 判斷方案（定案）

# 4.1 採用方案
## Ratio 方案 1 + B-lite+

### 定義
- **使用檔案級 ratio**，不是全文字數 ratio
- 只掃：
  - `.json`
  - `.md`
  - `.txt`
- 只掃 Patchouli 語系內容：
  - `zh_tw/`
  - `zh_cn/`
- 不掃 binary、不掃 `en_us` 內容做品質分析

### 計算公式
對某個 `book_root` + `lang`：

```python
effective_ratio = effective_cjk_files / total_text_files
```

若：
```python
effective_ratio >= patchouli_effective_translation_threshold
```
則視為該語系為「有效翻譯」。

---

# 4.2 有效 CJK 檔案定義

### JSON
- parse 後遞迴抽出字串欄位
- 若可用字串為 0 → 此檔不算有效翻譯檔
- 若含 CJK 的字串數 / 全字串數 >= 0.5 → 此檔視為有效 CJK 檔

### MD / TXT
- 讀成純文字
- 去除空白後若內容為空 → 不算有效翻譯檔
- 若 CJK 字元比例 >= 0.5 → 視為有效 CJK 檔

---

# 4.3 為何不用全文重型分析

原因：
1. 本次是流程優化，不應把 pre-scan 變成新瓶頸
2. Patchouli 格式多樣，做重型全文統計成本高
3. 檔案級 ratio 已足夠防止「空殼 zh_cn/zh_tw 誤判」
4. 可搭配 early stop 控制時間成本

---

# 4.4 Early stop 規則（必要）

為避免 Patchouli 預掃描拖慢整體時間：

對每個 `book_root` + `lang`：
- 在掃描檔案過程中，若已確定 ratio **必定 >= threshold**，可提前停止
- 若已確定 ratio **即使剩餘檔案全算有效也達不到 threshold**，也可提前停止

> 這是本版避免效能退化的關鍵要求

---

## 五、後端修改點

# 5.1 `lang_merger.py`

## 必改內容
1. `max_workers` 改成 `CPU // 2` capped version
2. 建立 Patchouli 預掃描結果快取，例如：

```python
patchouli_lang_effectiveness = {
    book_root: {
        "zh_tw": True,
        "zh_cn": False,
    }
}
```

3. 把該結果傳給 `_process_content_or_copy_file(...)`

---

# 5.2 `lang_merge_content.py`

## 必改內容
 façade 必須透傳：
- `patchouli_lang_effectiveness`
- 新增的 `lang_merger` 設定值（若目前架構需要顯式傳參）

---

# 5.3 `lang_merge_content_copy.py`

## 必改內容

### A. `only_process_lang=true` 時的 `zh_cn` 跳過邏輯
需加入：
- 讀 `skip_zh_cn_when_only_process_lang`
- 若條件成立，直接略過 `zh_cn`

### B. 一般模式下 `process_zh_cn_files=false` 時的 `zh_cn` 跳過邏輯
需加入：
- 讀 `process_zh_cn_files`
- 若關閉，直接略過 `zh_cn`

### C. Patchouli `en_us` skip 判斷
改成查：
```python
book_eval = patchouli_lang_effectiveness.get(book_root, {})
has_effective_zh_tw = book_eval.get("zh_tw", False)
has_effective_zh_cn = book_eval.get("zh_cn", False)
allow_zh_cn_skip = load_config_fn().get("lang_merger", {}).get(
    "patchouli_skip_en_us_when_zh_cn_exists",
    False,
)

should_skip_en_us = has_effective_zh_tw or (
    allow_zh_cn_skip and has_effective_zh_cn
)
```

> 不再允許只靠資料夾存在就跳過 `en_us`

---

# 5.4 `lang_merge_pipeline.py`

## 必改內容
`contains_cjk()` 對 `str` 分支加入 `lru_cache`

---

# 5.5 `jar_processor_extract.py`

## 必改內容
1. `translation` → `translator`
2. `max_workers` 改成 `CPU // 2` capped version

---

# 5.6 `config_manager.py`

## 必改內容
在 `DEFAULT_CONFIG["lang_merger"]` 補上：
```python
"process_zh_cn_files": True,
"skip_zh_cn_when_only_process_lang": False,
"patchouli_skip_en_us_when_zh_cn_exists": False,
"patchouli_effective_translation_threshold": 0.5,
```

並保持舊 config 深度合併後可自動補齊新欄位。

---

## 六、View / UI 必改內容

> 本次明確要求：不只後端，**View 上也要補上開關與語意說明**

# 6.1 需求目標

在 merge 或 lang merge 相關設定區中，新增以下 UI 控件：

1. `處理 zh_cn 檔案`
2. `只處理 lang 時跳過 zh_cn`
3. `Patchouli 允許 zh_cn 觸發跳過 en_us`
4. `Patchouli 有效翻譯比例門檻`

---

# 6.2 UI 控件建議型態

### A. `處理 zh_cn 檔案`
- 控件：`Switch`
- 綁定：`lang_merger.process_zh_cn_files`
- 預設：開啟

### B. `只處理 lang 時跳過 zh_cn`
- 控件：`Switch`
- 綁定：`lang_merger.skip_zh_cn_when_only_process_lang`
- 預設：關閉

### C. `Patchouli 允許 zh_cn 觸發跳過 en_us`
- 控件：`Switch`
- 綁定：`lang_merger.patchouli_skip_en_us_when_zh_cn_exists`
- 預設：關閉

### D. `Patchouli 有效翻譯比例門檻`
- 控件：`TextField` 或 `Slider`
- 綁定：`lang_merger.patchouli_effective_translation_threshold`
- 預設：`0.5`
- 可接受範圍建議：`0.0 ~ 1.0`

---

# 6.3 按鈕 / 開關語意說明（必填）

> 不能只放開關名稱，**必須在 UI 上補說明文字**，避免使用者誤解。

### 1. 處理 zh_cn 檔案
**標題：**
`處理 zh_cn 檔案`

**說明：**
`關閉後，合併流程會略過 zh_cn 檔案；開啟時則維持正常處理。`

---

### 2. 只處理 lang 時跳過 zh_cn
**標題：**
`只處理 lang 時跳過 zh_cn`

**說明：**
`僅在「只處理 lang」模式生效。開啟後，zh_cn 的 lang 檔會直接跳過，不進入後續合併流程。`

---

### 3. Patchouli 允許 zh_cn 觸發跳過 en_us
**標題：**
`Patchouli：允許 zh_cn 觸發跳過 en_us`

**說明：**
`開啟後，若 Patchouli 的 zh_cn 有效翻譯比例達門檻，會視為可用翻譯，並跳過對應的 en_us。關閉時僅 zh_tw 可觸發跳過。`

---

### 4. Patchouli 有效翻譯比例門檻
**標題：**
`Patchouli 有效翻譯比例門檻`

**說明：**
`用來判斷 zh_tw / zh_cn 是否已具備足夠翻譯內容。預設 0.5，代表至少一半的文字檔被判定為有效 CJK 翻譯。`

---

# 6.4 UI 互動規則

### 規則 A
若 `process_zh_cn_files = false`：
- `skip_zh_cn_when_only_process_lang` 可以保留顯示
- 但應加註「目前全域已停用 zh_cn 處理」
- 或直接 disable 該開關（推薦）

### 規則 B
`patchouli_effective_translation_threshold` 若輸入非法值：
- 應自動回退到 `0.5`
- 或顯示錯誤提示並阻止儲存

### 規則 C
開關變更後需確實寫回 config，並在下一次執行 merge 時生效

---

## 七、View 端驗證要求

1. 設定頁可看到新增的 4 個欄位
2. 每個欄位都有清楚說明文字
3. 開關切換後能正確寫回 config
4. 重新開啟頁面時可正確回填設定值
5. `process_zh_cn_files = false` 時，`skip_zh_cn_when_only_process_lang` 的互動行為合理（disable 或提示）
6. threshold 欄位輸入非法值時，不得寫出壞設定

---

## 八、Rejected approaches（必填）

### R1. 只看 `zh_cn/zh_tw` 目錄存在就跳過 `en_us`
**拒絕原因**：可能只是半成品或英文殘留，誤判率高。

### R2. 做全文字元級重型 ratio 分析
**拒絕原因**：成本高，與本次流程優化目標衝突。

### R3. 把 `zh_cn` 一般處理、`only_process_lang` 跳過、Patchouli `en_us` skip 三種行為混成同一個開關
**拒絕原因**：語意不清，工程師易做錯，使用者也難理解。

### R4. 不在 UI 顯示說明，只放開關名稱
**拒絕原因**：高機率造成使用者誤解設定效果。

---

## 九、實作順序（給執行者）

1. 先補 `config_manager.py` 預設值
2. 再做 `lang_merger.py` / `lang_merge_content_copy.py` 的主流程判斷
3. 做 `lang_merge_pipeline.py` 的 `contains_cjk()` cache
4. 做 `jar_processor_extract.py` 的 worker/config key 修正
5. 最後補 View/UI 開關與說明文字
6. 回頭做整體驗證

---

## 十、交付後驗證清單

```text
[ ] 已先備份修改檔案
[ ] DEFAULT_CONFIG 已補上 4 個新欄位
[ ] lang_merger.py 的 max_workers 已改為 CPU//2 capped version
[ ] jar_processor_extract.py 的 translation -> translator 已修正
[ ] jar_processor_extract.py 的 max_workers 已改為 CPU//2 capped version
[ ] zh_cn 一般處理開關已生效
[ ] only_process_lang 模式下跳過 zh_cn 開關已生效
[ ] Patchouli en_us skip 已改為 Ratio 方案 1 + B-lite+
[ ] patchouli_skip_en_us_when_zh_cn_exists 已生效
[ ] patchouli_effective_translation_threshold 已生效
[ ] contains_cjk() 已僅對 str 分支加入 cache
[ ] View 上已補 4 個設定控件
[ ] 每個控件都有語意說明文字
[ ] config 可成功寫回並重新載入
[ ] threshold 非法值有保護機制
[ ] python -m py_compile 相關檔案通過
[ ] pytest / 既有測試通過
[ ] 實測 merge 流程功能正常、無明顯時間退化
```

---

## 十一、最終交付結論

這份 v2 設計稿可直接交給其他人執行。

本版明確要求：
- **後端 merge 邏輯要改**
- **View / UI 開關也要補**
- **每個開關都要有語意說明**

本次准許實作的只有：
- P0 修正版（Patchouli ratio + B-lite+）
- P1 修正版（`contains_cjk()` cache）
- P3 修正版（worker 收斂 + config key 修正）
- `zh_cn` 行為開關
- View/UI 設定與說明補齊
