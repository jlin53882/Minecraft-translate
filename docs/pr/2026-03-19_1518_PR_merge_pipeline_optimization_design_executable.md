# 檔案合併管線（Merge）優化設計稿｜可執行版

> 日期：2026-03-19 15:18
> 狀態：可交付他人執行
> 適用版本：minecraft-translator-flet（`translation_tool/core/lang_merger.py`、`lang_merge_content_copy.py`、`lang_merge_pipeline.py`、`jar_processor_extract.py`）
> 目的：只保留**已由現況程式碼驗證**、且**可安全落地**的優化項目，避免錯誤優化導致行為退化

---

## 一、結論摘要

本次稽核後，原始報告中的 5 個提案不應全部直接執行。

### 可執行項目
1. **P0 修正版**：將 Patchouli `book_root` 是否存在 `zh_cn/zh_tw` 的判斷，從每次線性掃描改為預先建立 root set
2. **P1 修正版**：`contains_cjk()` 僅對 `str` 分支加入 memoization，避免重複 regex 掃描
3. **P3 修正版（必要）**：將 `max_workers` 上限寫死在程式碼內，以 `CPU 核心數 // 2` 為上限；並修正 `jar_processor_extract.py` 錯誤的 config key

### 不建議執行項目
1. **P2 JSON5 / comment fallback**：原報告前提與現況流程不一致，暫不納入本次設計
2. **SHA256 hash cache**：收益低且提案存在碰撞/記憶體風險，暫不納入本次設計

---

## 二、驗證依據（已實際讀碼）

### 2.1 Patchouli 掃描熱點存在
- `translation_tool/core/lang_merger.py:126-145`
  - 目前先建立 `all_files_cache = [n.lower().replace("\\", "/") for n in zf.namelist()]`
  - 然後把 `all_files_cache` 傳給 `_process_content_or_copy_file(...)`
- `translation_tool/core/lang_merge_content_copy.py:86-92`
  - 目前 `book_root` 的 `has_cn_or_tw` 判斷是：
  ```python
  has_cn_or_tw = any(
      n.startswith(book_root) and ("/zh_cn/" in n or "/zh_tw/" in n)
      for n in all_files_cache
  )
  ```
- 結論：此處確實存在重複 O(n) 掃描，可安全優化

### 2.2 `contains_cjk()` 確實重複被呼叫
- `translation_tool/core/lang_merge_pipeline.py:47-75`
  - `contains_cjk(v)` 支援 `str/list/dict`
  - `is_pure_english(v)` 內部也會再呼叫 `contains_cjk(v)`
- `translation_tool/core/lang_merge_pipeline.py:188-227`
  - 同一輪 key 處理內，`final_tw / tw_val / cn_val / english_source` 都可能多次進入 `contains_cjk`
- 結論：有優化空間，但應只針對 `str` 做 cache，避免結構型態 cache 複雜化

### 2.3 原 P2 前提不成立
- `translation_tool/core/lang_merger.py:98-109`
  - 目前主流程只把 `zh_cn / zh_tw / en_us` 視為 lang 模組來源
  - 其他 `/lang/*.json`（例如 `pt_br.json`）**目前未納入這條 merge 主流程**
- `translation_tool/core/lang_merge_zip_io.py:70-77`
  - `_read_json_from_zip()` parse 失敗時是 `log_warning(...)` 後 `return {}`
  - 並非原報告所稱的「失敗就整檔隔離」
- 結論：原 P2 提案不符合現況，先排除

### 2.4 原 SHA256 cache 不值得做
- `translation_tool/core/jar_processor_extract.py:70-77`
  - 每個 member 只讀一次 `source_data`、算一次 source hash
  - 若目標檔存在，才再算 existing hash
- `translation_tool/core/jar_processor_extract.py:115`
  - 另有更優先的問題：讀 config 用了錯的 key：`translation`，應為 `translator`
- 結論：先修 worker 與 config key，比做 hash cache 更有價值

---

## 三、執行範圍

### 3.1 允許修改檔案
1. `translation_tool/core/lang_merger.py`
2. `translation_tool/core/lang_merge_content.py`
3. `translation_tool/core/lang_merge_content_copy.py`
4. `translation_tool/core/lang_merge_pipeline.py`
5. `translation_tool/core/jar_processor_extract.py`

### 3.2 本次不修改檔案
1. `translation_tool/core/lang_merge_zip_io.py`
2. `config.json`
3. 測試資料或 fixture

---

## 四、設計內容

# P0｜Patchouli `book_root` 掃描優化（可執行）

## 4.1 問題描述

目前每個 Patchouli 檔案在 `lang_merge_content_copy.py` 都會重複掃描整個 `all_files_cache`，用來判斷：
- 該 `book_root` 底下是否已有 `zh_cn/` 或 `zh_tw/`

此判斷目前為 O(n) 線性掃描，且會隨著內容檔數量重複發生。

## 4.2 目標

將判斷改為：
- 在 `lang_merger.py` 開始平行處理前
- 先一次性建立 `localized_patchouli_roots: set[str]`
- 後續每次只做 O(1) membership check：
  ```python
  has_cn_or_tw = book_root in localized_patchouli_roots
  ```

## 4.3 設計原則

- **不改變既有行為語義**
- 判斷條件仍然是：
  > 「這個 `book_root` 底下是否存在 `zh_cn/` 或 `zh_tw/`」
- **不可偷換成**「整個 ZIP 只要有任何 `zh_cn/zh_tw` 就算 true」

## 4.4 具體修改

### 檔案 A：`translation_tool/core/lang_merge_content_copy.py`

#### 目前簽名
```python
def process_content_or_copy_file_impl(
    zf: zipfile.ZipFile,
    input_path: str,
    rules: list,
    output_dir: str,
    *,
    only_process_lang: bool = False,
    all_files_cache: List[str] | None = None,
    ...
) -> Dict[str, Any]:
```

#### 修改後簽名
```python
def process_content_or_copy_file_impl(
    zf: zipfile.ZipFile,
    input_path: str,
    rules: list,
    output_dir: str,
    *,
    only_process_lang: bool = False,
    all_files_cache: List[str] | None = None,
    localized_patchouli_roots: set[str] | None = None,
    ...
) -> Dict[str, Any]:
```

#### `book_root` 判斷邏輯改為
```python
has_cn_or_tw = False
if localized_patchouli_roots is not None:
    has_cn_or_tw = book_root in localized_patchouli_roots
elif all_files_cache:
    has_cn_or_tw = any(
        n.startswith(book_root) and ("/zh_cn/" in n or "/zh_tw/" in n)
        for n in all_files_cache
    )
```

> 說明：保留 `all_files_cache` fallback，避免相容層一次改太大時直接爆掉。

---

### 檔案 B：`translation_tool/core/lang_merge_content.py`

#### façade 需要同步透傳新參數
目前：
```python
def _process_content_or_copy_file(
    zf,
    input_path: str,
    rules: list,
    output_dir: str,
    only_process_lang: bool = False,
    all_files_cache=None,
):
```

修改為：
```python
def _process_content_or_copy_file(
    zf,
    input_path: str,
    rules: list,
    output_dir: str,
    only_process_lang: bool = False,
    all_files_cache=None,
    localized_patchouli_roots=None,
):
```

並在呼叫 `process_content_or_copy_file_impl(...)` 時同步傳入。

---

### 檔案 C：`translation_tool/core/lang_merger.py`

#### 新增一次性建表流程
在 ThreadPool 啟動前：
1. 先保留既有 `all_files_cache`
2. 再新增一個 `localized_patchouli_roots = set()`
3. 掃過 `all_files_cache`，把符合以下條件的 path 對應 root 收進 set：
   - path 中含 `/patchouli_books/`、`/book/`、`/manual/`、`/guidebook/`
   - 且 path 中含 `/zh_cn/` 或 `/zh_tw/`
   - root 必須與 `get_patchouli_book_root()` 的語義一致

#### 實作要求
不要在 `lang_merger.py` 重新發明另一套 root 邏輯。應抽出共用 helper，避免：
- `lang_merger.py` 算的 root
- `lang_merge_content_copy.py` 算的 root
兩邊規則不一致。

### 建議做法
把 `get_patchouli_book_root()` 抽成 module-level helper，例如：
```python
def detect_patchouli_book_root(path: str, patchouli_dirs: list[str]) -> tuple[str, str] | None:
    ...
```

然後：
- `lang_merge_content_copy.py` 使用它
- `lang_merger.py` 建 set 時也使用它

> 若本次不想搬太大，也可先複製邏輯，但 PR 說明需明講這是暫時 duplication，後續要收斂。

---

## 4.5 驗證標準

### 功能驗證
1. 有 `zh_cn/zh_tw` 的 Patchouli 書本，`en_us` 原件仍會被正確跳過
2. 沒有 `zh_cn/zh_tw` 的 Patchouli 書本，仍會進 `待翻譯`
3. 一般非 Patchouli 檔案行為不變

### 效能驗證
- 比對優化前後 merge 同一批測試 ZIP/JAR 的總耗時
- 不要求硬性宣稱 `10x+`
- 報告時只寫實測數字，不寫預估神話值

---

# P1｜`contains_cjk()` 字串 memoization（可執行）

## 5.1 問題描述

目前 `contains_cjk(v)` 會遞迴處理 `str/list/dict`，同一批資料中的相同字串可能被多次 regex 掃描。

## 5.2 目標

只針對 `str` 分支做 cache：
- 保持既有 `contains_cjk(v)` API 不變
- 不對 `list/dict` 做複雜 cache
- 降低 regex 重複匹配次數

## 5.3 修改方式

### 檔案：`translation_tool/core/lang_merge_pipeline.py`

#### 目前
```python
def contains_cjk(v: Any) -> bool:
    if isinstance(v, str):
        return CJK_RE.search(v) is not None
    if isinstance(v, list):
        return any(contains_cjk(x) for x in v)
    if isinstance(v, dict):
        return any(contains_cjk(x) for x in v.values())
    return False
```

#### 修改後建議
```python
from functools import lru_cache

@lru_cache(maxsize=4096)
def _contains_cjk_str(s: str) -> bool:
    return CJK_RE.search(s) is not None

def contains_cjk(v: Any) -> bool:
    if isinstance(v, str):
        return _contains_cjk_str(v)
    if isinstance(v, list):
        return any(contains_cjk(x) for x in v)
    if isinstance(v, dict):
        return any(contains_cjk(x) for x in v.values())
    return False
```

## 5.4 不採用方案

### 不採用 `id(v)` 當 cache key
原因：
- 只能快取同一個物件實例
- 對內容相同但不同實例無效
- 對結構型資料效益不穩定
- 複雜度高於收益

---

## 5.5 驗證標準

1. `contains_cjk()` 對 `str/list/dict` 的返回結果與修改前一致
2. `_process_single_mod()` 的輸出 `pending` / `final_tw` 不得發生語義改變
3. 可用計時比較前後耗時，但不強制承諾固定倍數

---

# P3｜ThreadPool `max_workers` 上限收斂（必要執行）

## 6.1 問題描述

### 問題 A：`lang_merger.py` fallback 過大
- `translation_tool/core/lang_merger.py:126`
```python
max_workers = load_config().get("translator", {}).get("parallel_execution_workers") or os.cpu_count()
```
若 config 無值或值異常，會直接退回 `os.cpu_count()`。

### 問題 B：`jar_processor_extract.py` config key 寫錯
- `translation_tool/core/jar_processor_extract.py:115`
```python
max_workers = load_config().get('translation', {}).get('parallel_execution_workers') or os.cpu_count()
```
此處 key 為 `translation`，但專案其他位置與 `config_manager.py` 都是 `translator`。

### 問題 C：上限策略不一致
- `config_manager.py` 預設值是 `max(1, os.cpu_count() // 2)`
- 執行時卻可能 fallback 到完整 CPU 數

## 6.2 目標

統一規則為：
- **最大上限固定為 `CPU 核心數 // 2`**
- config 若有值，可往下調，但不可超上限
- 若 config 缺失/異常，直接使用上限值

## 6.3 修改方式

### 共用邏輯
建議抽成一小段 helper（可各檔內先各自實作，後續再收斂）：
```python
cpu_count = os.cpu_count() or 2
max_allowed_workers = max(1, cpu_count // 2)

config_workers = load_config().get("translator", {}).get("parallel_execution_workers")
if isinstance(config_workers, int) and config_workers > 0:
    max_workers = min(config_workers, max_allowed_workers)
else:
    max_workers = max_allowed_workers
```

### 檔案 A：`translation_tool/core/lang_merger.py`
將原本：
```python
max_workers = load_config().get("translator", {}).get("parallel_execution_workers") or os.cpu_count()
```
改為上述 capped version。

### 檔案 B：`translation_tool/core/jar_processor_extract.py`
將原本：
```python
max_workers = load_config().get('translation', {}).get('parallel_execution_workers') or os.cpu_count()
```
改為：
1. key 修正成 `translator`
2. 套用相同 capped version

## 6.4 本機已驗證事實
- 本機 `os.cpu_count()` = `16`
- 因此上限為 `8`
- 若 config 設 `4`，最終應為 `4`
- 若 config 設 `24`，最終應被 cap 成 `8`

---

## 6.5 驗證標準

1. `lang_merger.py` 與 `jar_processor_extract.py` 都使用相同 worker 上限邏輯
2. `translator.parallel_execution_workers=4` 時，實際值為 4
3. 設為大於上限時，實際值被 cap
4. config 缺值時，實際值為 `max(1, cpu_count // 2)`

---

## 五、Rejected approaches（必填）

### R1. 用 `bool(_prefix_map["/zh_cn/"] | _prefix_map["/zh_tw/"])` 判斷 Patchouli 是否已翻譯
**拒絕原因**：
這只會回答「整個 ZIP 有沒有 zh_cn/zh_tw」，無法回答「特定 `book_root` 底下有沒有 zh_cn/zh_tw」，會誤判。

### R2. 用 `id(v)` 當 `contains_cjk()` cache key
**拒絕原因**：
快取只對同一個 Python 物件實例有效，對內容相同但非同一實例的字串幫助有限，且不如直接 cache `str` 清楚。

### R3. 為 SHA256 只用前 1KB 當 hash cache key
**拒絕原因**：
存在碰撞風險，可能錯誤判斷不同檔案內容相同。

### R4. 先把整個 ZIP 全部讀進 `content_map`
**拒絕原因**：
提高記憶體壓力，對大 JAR 不友善，收益不足。

### R5. 本次直接加 JSON5/comment fallback parser
**拒絕原因**：
原始報告問題前提未被現況程式碼證實，且 parser 容易因字串/escaped quote 處理不完整造成誤解析。

---

## 六、實作順序（給執行者）

1. **先做 P3**：風險最低、收益穩定、順便修正錯誤 config key
2. **再做 P0**：需小心保持 `book_root` 判斷語義一致
3. **最後做 P1**：小改動、低風險

---

## 七、交付後驗證清單

```text
[ ] 已先備份修改檔案
[ ] lang_merger.py 的 max_workers 已改為 CPU//2 capped version
[ ] jar_processor_extract.py 的 translation -> translator 已修正
[ ] jar_processor_extract.py 的 max_workers 已改為 CPU//2 capped version
[ ] Patchouli has_cn_or_tw 判斷已改為使用 localized_patchouli_roots
[ ] contains_cjk() 已僅對 str 分支加入 cache
[ ] python -m py_compile translation_tool/core/*.py
[ ] pytest / 既有測試通過
[ ] 實測一批 merge 任務，功能結果與修改前一致
[ ] 有提供前後耗時數據（只填實測值，不寫估算神話）
```

---

## 八、給執行者的注意事項

1. **不要順手實作 P2 或 SHA256 cache**，本次設計明確排除
2. **不要改變 Patchouli 判斷語義**，只允許換資料結構，不允許換判斷定義
3. **不要把 config 當唯一來源**，程式碼內必須有 `CPU // 2` 上限保護
4. 若要抽 helper，請控制改動範圍，避免一次重構太大造成 regression

---

## 九、最終交付結論

這份設計稿可直接交給其他人執行；本次准許實作的只有：
- **P0 修正版**
- **P1 修正版**
- **P3 修正版**

其餘提案一律視為**本次不做**。
