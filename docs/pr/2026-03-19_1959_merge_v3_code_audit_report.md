# Merge v3 程式碼稽核報告｜待修清單

> 日期：2026-03-19 19:59
> 稽核對象：本地已修改之 merge pipeline / view / config 相關程式碼
> 對照基準：`docs/pr/2026-03-19_1612_PR_merge_pipeline_optimization_design_v3.md`
> 稽核結論：**部分完成，但尚未達到 v3 可驗收狀態**

---

## 一、總結

目前這版修改**不是完全錯**，但也**不能直接視為照 v3 完成**。

### 目前判定
- **已完成：約 60~70%**
- **可確認已做的部分：config 欄位、部分後端行為、UI 開關雛形、jar_processor_extract config key 修正**
- **仍需補修的核心項目：Patchouli effectiveness 快取、worker capped version、runtime guard 完整化、儲存正規化驗證、memoization 與設計稿一致性**

---

## 二、已確認完成的項目

### 1. `config_manager.py` 已補上 v3 需要的新欄位
已確認存在：
- `process_zh_cn_files`
- `skip_zh_cn_when_only_process_lang`
- `patchouli_skip_en_us_when_zh_cn_exists`
- `patchouli_effective_translation_threshold`

**判定：✅ 完成**

---

### 2. `jar_processor_extract.py` 已修正 `translation -> translator`
已確認：
```python
load_config().get("translator", {})
```

**判定：✅ 完成**

---

### 3. `merge_view.py` 已補 UI 控件
已確認畫面上已有：
- `處理 zh_cn 檔案`
- `只處理 lang 時跳過 zh_cn`
- `Patchouli：允許 zh_cn 觸發跳過 en_us`
- `Patchouli 有效翻譯比例門檻`

且已看到：
- 互鎖 disable 雛形
- disabled note 顯示

**判定：✅ 部分完成（UI 雛形已存在）**

---

### 4. `lang_merge_content_copy.py` 已有 zh_cn 開關邏輯
已確認存在：
- `process_zh_cn_files`
- `skip_zh_cn_when_only_process_lang`
- `patchouli_skip_en_us_when_zh_cn_exists`
- `patchouli_effective_translation_threshold`

**判定：✅ 部分完成（已有核心判斷）**

---

## 三、未完成 / 與設計稿不一致項目（必修）

# 1. Patchouli effectiveness 沒有做快取，反而每次重算

## 現況
`lang_merge_content_copy.py` 目前每次處理進入 Patchouli 路徑時，都會重新執行：

```python
eff = _compute_patchouli_lang_effectiveness(...)
```

而 `_compute_patchouli_lang_effectiveness()` 內部又會：
- 掃 `zf.namelist()`
- 讀取 `zh_tw/zh_cn` 內容檔
- 重新計算 ratio

## 問題
這與 v3 設計稿要求不一致。設計稿原本要求：
- 在 `lang_merger.py` 預掃描一次
- 建立 `patchouli_lang_effectiveness` 快取
- 後續 `_process_content_or_copy_file()` 直接查快取

目前做法的風險是：
- 同一個 `book_root` 底下多個檔案會反覆重算
- 可能把原本要做的效能優化吃回去

## 必修要求
- 改成 **book_root 級快取**
- 不允許每個檔案都重算整本書的 effectiveness

**判定：❌ 未完成**

---

# 2. `lang_merge_content.py` façade 沒有透傳預掃描結果

## 現況
目前 `_process_content_or_copy_file()` 仍然只有：

```python
def _process_content_or_copy_file(..., only_process_lang=False, all_files_cache=None)
```

沒有：
- `patchouli_lang_effectiveness`
- 或任何預掃描結果參數

## 問題
這代表設計稿中的「預掃描 → 傳遞 → 查快取」架構沒有落地。

## 必修要求
- façade 必須新增對應參數
- `lang_merger.py` 預掃描結果需透過 façade 傳入 `process_content_or_copy_file_impl()`

**判定：❌ 未完成**

---

# 3. `contains_cjk()` 的 memoization 實作與設計稿不一致

## 現況
目前 `lang_merge_pipeline.py` 使用的是：

```python
_value_cjk_cache = {}
vid = id(v)
```

## 問題
這不是 v3 設計稿定案的版本。

v3 原本要求的是：
- 對 `str` 分支使用 `lru_cache`
- 不採用 `id(v)` 作為主要方案

`id(v)` 版本的問題：
- 只對同一個 Python 物件實例有效
- 對內容相同但不同實例的字串效果不穩
- 不符合設計稿

## 必修要求
改回設計稿版本，例如：

```python
from functools import lru_cache

@lru_cache(maxsize=4096)
def _contains_cjk_str(s: str) -> bool:
    return CJK_RE.search(s) is not None
```

**判定：❌ 未完成（實作偏離設計稿）**

---

# 4. `max_workers` 邏輯沒有真正 capped 到 `CPU // 2`

## 現況
`lang_merger.py`：
```python
if _workers_cfg is None:
    max_workers = min(32, max(1, (os.cpu_count() or 4) // 2))
else:
    max_workers = _workers_cfg
```

`jar_processor_extract.py`：
```python
max_workers = load_config().get("translator", {}).get("parallel_execution_workers")
if max_workers is None:
    max_workers = min(32, max(1, (os.cpu_count() or 4) // 2))
```

## 問題
當 config 有值時，目前**直接使用 config 值**，沒有再 cap 到 `CPU // 2`。

這與 v3 / 最終定案不一致。

## 正確要求
應統一改成：

```python
cpu_count = os.cpu_count() or 2
max_allowed_workers = max(1, cpu_count // 2)

config_workers = load_config().get("translator", {}).get("parallel_execution_workers")
if isinstance(config_workers, int) and config_workers > 0:
    max_workers = min(config_workers, max_allowed_workers)
else:
    max_workers = max_allowed_workers
```

## 驗證要求
- config=4 → worker=4
- config=32 → worker=CPU//2
- config 缺值 → worker=CPU//2

**判定：❌ 未完成**

---

# 5. 儲存正規化（normalization）尚未證明完成

## 現況
`merge_view.py` 目前只看到 UI 層：
- toggle change 時把依賴開關 value 設成 False
- disable / 顯示 note

但目前稽核到的檔案中，**尚未看到真正儲存 config 時的 normalization 邏輯**。

## 設計稿要求
若：
```python
process_zh_cn_files == False
```
則儲存前必須強制：
```python
skip_zh_cn_when_only_process_lang = False
patchouli_skip_en_us_when_zh_cn_exists = False
```

## 必修要求
- 找出實際寫回 config 的 service / controller
- 在儲存前加入 normalization
- 不能只做 UI 當下值清空

**判定：❌ 未驗證 / 視為未完成**

---

# 6. runtime guard 沒有完整落實

## 現況
目前 `lang_merge_content_copy.py` 有部分 runtime guard：
- `process_zh_cn_files == False` 時，會跳過 `zh_cn`

但 Patchouli 判斷中：
```python
allow_zh_cn = bool(merger_cfg.get("patchouli_skip_en_us_when_zh_cn_exists", False))
```

沒有再與：
```python
process_zh_cn_files
```
做強制聯動。

## 問題
若 config 被手改成：
- `process_zh_cn_files = false`
- `patchouli_skip_en_us_when_zh_cn_exists = true`

runtime 目前仍可能讀到 `allow_zh_cn=True`。

## 必修要求
必須改成：

```python
process_zh_cn = merger_cfg.get("process_zh_cn_files", True)
allow_zh_cn = False if not process_zh_cn else bool(
    merger_cfg.get("patchouli_skip_en_us_when_zh_cn_exists", False)
)
```

**判定：❌ 未完成**

---

## 四、部分完成但需補強項目

# 7. `merge_view.py` 有 UI 互鎖，但說明文字需再檢查一致性

## 現況
目前有：
- 正常說明文字
- disabled note

## 需補查
- 是否所有 disabled 控件都同時具備：
  - 標題
  - 正常說明
  - disabled 原因文字
  - 灰化狀態
- 是否在 reload config 後仍能保持互鎖一致

**判定：⚠️ 部分完成，需再驗證**

---

## 五、建議修正順序（給實作者）

1. **先修 `max_workers` capped version**（兩處都修）
2. **再修 Patchouli effectiveness 快取架構**
3. **補 façade 透傳預掃描結果**
4. **把 `contains_cjk()` 改回 `lru_cache` 方案**
5. **補儲存正規化**
6. **補完整 runtime guard**
7. **最後做 UI reload / config round-trip 驗證**

---

## 六、驗收結論

### 本次不能直接驗收通過
原因：
- 還有多個核心點與 v3 設計稿不一致
- 特別是 Patchouli effectiveness 快取與 worker capped version，屬於本次設計的核心要求

### 驗收判定
- **已完成：60~70%**
- **狀態：退回補修**

---

## 七、最終一句話（給修正者）

這版已做出雛形，但仍需補修以下 6 點才可驗收：

1. Patchouli effectiveness 改為快取，不得每檔重算
2. `lang_merge_content.py` façade 要透傳預掃描結果
3. `contains_cjk()` 改成 `lru_cache` 版
4. `max_workers` 要真正 cap 到 `CPU // 2`
5. 補儲存正規化
6. 補完整 runtime guard
