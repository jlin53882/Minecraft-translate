# JAR 抽取（Extract）優化設計稿｜最終可執行版

> 日期：2026-03-19 16:28
> 狀態：可交付他人執行
> 適用版本：minecraft-translator-flet（`jar_processor*.py` 模組）
> 範圍：lang 抽取、book（Patchouli）抽取、preview 預覽、抽取流程設定一致性
> 原則：只保留**已由現況程式碼驗證**、且**可安全落地**的項目；避免高風險「看似優化、實際可能退化」的提案

---

## 一、最終結論

本次 JAR 抽取優化不採用激進方案，最後收斂為：

### 本次必做
1. **P0｜全專案 config key 稽核：`translation` → `translator`（限真正屬於 config key 的讀取處）**
2. **P1｜`max_workers` 與合併管線統一，採 `CPU // 2` capped version**
3. **P2｜`lang_codes` 改為 config 驅動（功能擴充，非效能優化）**
4. **P2｜確認 preview 現況已使用 metadata 統計，不再誤列為 I/O 優化項**
5. **P2｜`find_jar_files()` 可做 `os.walk` vs `glob.glob` 實測比較，但只有實測穩定更快才允許替換**

### 本次不做
1. 不做整包 ZIP preload 到記憶體 dict
2. 不做 SHA256 content memo
3. 不做 ThreadPool 掃目錄
4. 不做 `cpu * 3` 或 `max 32` 這類激進 worker 放大策略
5. 不把 preview 誤寫成「需要去掉 read()」的優化項（因為現況本來就沒 read）

---

## 二、現況驗證（已讀碼確認）

### 2.1 `jar_processor_extract.py`
已確認：
- `extract_from_jar_impl()` 對每個 matched member：
  - 讀取 `source_data`
  - 計算 SHA256
  - 若目標檔已存在，讀取 existing file 再比對 hash
- 現況 **不是同一 member 重複讀多次 ZIP 內容**
- 不適合直接改成「整包 ZIP 全讀進記憶體」

### 2.2 `jar_processor_extract.py` 存在錯誤 config key
已確認現況：
```python
max_workers = load_config().get('translation', {}).get('parallel_execution_workers') or os.cpu_count()
```
此處 `translation` 與專案既有設定命名不一致，需進行全專案稽核。

### 2.3 `jar_processor_discovery.py`
已確認現況：
```python
for root, _, files in os.walk(folder_path):
    ...
```
目前確實使用 `os.walk()`。

### 2.4 `jar_processor.py`
已確認現況 lang 抽取 regex 只支援：
- `en_us`
- `zh_cn`
- `zh_tw`

### 2.5 `jar_processor_preview.py`
已確認現況 preview 流程：
- 使用 `zf.infolist()`
- 使用 `member.file_size`
- **沒有 `zf.open()` / `read()` 內容**

因此「preview 重複 I/O」不是成立的優化項。

---

## 三、P0｜全專案 config key 稽核（必要）

## 3.1 目標
確認整個專案中，所有真正屬於「translator 設定區塊」的讀取邏輯，是否有誤寫成 `translation`。

## 3.2 注意事項
### 不允許直接全域取代
原因：
- `translation` 可能出現在一般變數名、文案、函式語意中
- 只有**確定是 config key 讀取**時，才可改為 `translator`

## 3.3 執行方式
### 全專案搜尋以下模式
- `get("translation"`
- `['translation']`
- `config.get("translation"`
- 其他實際存在的 config 存取寫法

### 每個命中項目要分三類
1. **需修改**：明確屬於 config key，應改成 `translator`
2. **不用修改**：只是變數/文案/其他語意
3. **需人工確認**：無法單靠表面判定

## 3.4 最低交付要求
執行者不能只回報「已修 `jar_processor_extract.py`」，而必須交付：
- 全專案稽核結果
- 修改清單
- 未修改理由

---

## 四、P1｜`max_workers` 與合併管線統一（必要）

## 4.1 設計原則
JAR 抽取流程的 worker 控制邏輯，**必須與 merge pipeline 完全一致**。

### 不採用
- `cpu * 3`
- `min(32, cpu * 3)`
- 任何比 merge pipeline 更激進的策略

### 採用
```python
cpu_count = os.cpu_count() or 2
max_allowed_workers = max(1, cpu_count // 2)

config_workers = load_config().get("translator", {}).get("parallel_execution_workers")
if isinstance(config_workers, int) and config_workers > 0:
    max_workers = min(config_workers, max_allowed_workers)
else:
    max_workers = max_allowed_workers
```

## 4.2 為什麼沿用合併管線策略
1. 專案內策略一致，降低維護成本
2. 抽取流程是 ZIP 讀取 + hash + 磁碟寫入的混合型負載，不適合暴力擴 thread
3. Windows 桌面環境下，過多 thread 可能導致磁碟競爭、hash 競爭與 context switch 增加
4. 第一版優先求穩定與可驗證，不追求理論峰值

## 4.3 套用位置
至少包含：
- `translation_tool/core/jar_processor_extract.py`
- 若全專案稽核發現其他抽取相關模組也有相同 worker 邏輯，需一併統一

## 4.4 驗證要求
若本機 `os.cpu_count() == 16`：
- 上限應為 `8`

需驗證：
1. config 設 `4` → 實際 `max_workers = 4`
2. config 設 `32` → 實際 `max_workers = 8`
3. config 缺值 → 實際 `max_workers = 8`
4. config 非 int / 非正整數 → fallback 到 `8`

---

## 五、P2｜`lang_codes` 改 config 驅動（功能擴充）

> 這條是功能擴充，不是效能優化。

## 5.1 目標
讓 lang 抽取支援由 config 指定語言清單，而不是寫死只有：
- `en_us`
- `zh_cn`
- `zh_tw`

## 5.2 現況
目前 `jar_processor.py` 內：
```python
lang_file_regex = re.compile(
    r"(?:assets/([^/]+)/)?lang/(en_us|zh_cn|zh_tw)\.(json|lang)$", re.IGNORECASE
)
```

## 5.3 修改方向
新增 config：
```json
{
  "jar_extractor": {
    "lang_codes": ["en_us", "zh_cn", "zh_tw", "ja_jp", "ko_kr", "ru_ru"]
  }
}
```

然後在 `jar_processor.py` 組出 regex：
```python
config = load_config()
lang_codes = config.get("jar_extractor", {}).get("lang_codes", ["en_us", "zh_cn", "zh_tw"])
lang_codes_lower = [c.lower() for c in lang_codes]
lang_codes_str = "|".join(map(re.escape, lang_codes_lower))
lang_file_regex = re.compile(
    rf"(?:assets/([^/]+)/)?lang/({lang_codes_str})\.(json|lang)$",
    re.IGNORECASE,
)
```

## 5.4 驗證要求
1. 預設不填 config 時，行為與目前一致
2. 新增 `ja_jp` / `ko_kr` 後，能被 preview 與 extract 正確匹配
3. 不得破壞原本 `en_us / zh_cn / zh_tw` 抽取行為

---

## 六、P2｜`find_jar_files()` 替換策略（僅允許 benchmark 驅動）

## 6.1 現況
目前使用：
```python
os.walk(folder_path)
```

## 6.2 允許的優化方向
可額外實驗：
```python
glob.glob(os.path.join(folder_path, "**", "*.jar"), recursive=True)
```

## 6.3 限制
### 不能直接定案為「一定更快」
因為速度會受以下因素影響：
- Windows 版本
- Python 版本
- 磁碟類型（SSD / HDD / 網路磁碟）
- 目錄深度與檔案數量

### 不採用 ThreadPool 掃子目錄版
原因：
- 容易重複掃描
- 邏輯複雜度升高
- 在 HDD / 網路磁碟下不一定更快
- 缺乏穩定收益證據

## 6.4 最終要求
若要替換 `os.walk`，必須提供：
1. 同一目錄下 `os.walk` vs `glob.glob` 的實測數據
2. 至少 3 次測量結果
3. 確認排序與輸出一致性
4. 若無明顯穩定優勢，維持 `os.walk`

---

## 七、P2｜Preview 現況說明（不列入優化項）

## 7.1 結論
`jar_processor_preview.py` 目前已使用：
- `zf.infolist()`
- `member.file_size`

### 因此：
- 不存在「preview 對每個 matched 都 `zf.open()` + `read()`」的現況問題
- 不應把這段列為優化項目

## 7.2 可做的僅限小整理
若有需要，僅可做：
- 補註解
- 規格文件修正
- 共用輔助函式（若真有重複）

但不列為本次優化主項。

---

## 八、本次明確不做的提案

### 8.1 不做整包 ZIP preload
**拒絕原因**：
- 多 JAR 並行時記憶體壓力大
- 風險高
- 收益未被證明

### 8.2 不做 SHA256 content memo
**拒絕原因**：
- 會把大量 bytes 保留在記憶體中
- 收益不穩定
- 非主要瓶頸

### 8.3 不做 `cpu * 3`
**拒絕原因**：
- 與 merge pipeline 策略不一致
- 對混合型負載過於激進
- 易導致磁碟競爭與不穩定

### 8.4 不做 ThreadPool 掃目錄
**拒絕原因**：
- 邏輯複雜
- 收益不穩
- 容易引入新問題

### 8.5 不把 preview 誤寫成 I/O 優化項
**拒絕原因**：
- 現況描述不成立
- 不應在設計稿中保留錯誤前提

---

## 九、實作順序（給執行者）

1. **先做 P0：全專案 config key 稽核**
2. **再做 P1：worker 邏輯與 merge pipeline 對齊**
3. **做 P2：lang_codes config 化**
4. **視情況做 P2：`os.walk` vs `glob.glob` benchmark**
5. **修正文檔：確認 preview 不列為 I/O 優化項**
6. **跑回歸驗證**

---

## 十、交付後驗證清單

```text
[ ] 已先備份修改檔案
[ ] 已完成全專案搜尋 translation / translator config key 使用處
[ ] 已提交「需修改 / 不用修改 / 需人工確認」稽核結果
[ ] jar_processor_extract.py 的 config key 已修正為 translator
[ ] 其他應屬 translator 的 config key 讀取處已同步修正
[ ] jar_processor_extract.py 的 max_workers 已改為與 merge pipeline 相同邏輯
[ ] config 設 4 時，實際 worker = 4
[ ] config 設 32 時，實際 worker = CPU//2 上限
[ ] config 缺值時，實際 worker = CPU//2 上限
[ ] lang_codes 已可由 config 驅動
[ ] 預設不填 lang_codes 時，舊行為保持一致
[ ] 新增 ja_jp / ko_kr 後能正確匹配 extract / preview
[ ] 若替換 find_jar_files()，已附上 os.walk vs glob.glob 實測數據
[ ] 若無穩定優勢，維持 os.walk
[ ] preview 不再被列為錯誤的 I/O 優化項
[ ] python -m py_compile 相關檔案通過
[ ] pytest / 既有測試通過
[ ] 實測抽取流程功能正常、無明顯時間退化
```

---

## 十一、最終交付結論

這份設計稿可直接交給其他人執行。

本版定案重點：
- **P0 做成全專案 config key 稽核**
- **P1 與合併管線完全對齊**
- **P2 只保留低風險功能擴充與 benchmark 驅動替換**
- **所有高風險、記憶體壓力大、收益不確定的提案全部排除**
