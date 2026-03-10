# app 資料夾程式分析報告

> 專案：`Minecraft_translator_flet`
> 範圍：`app/`（以非 UI 結構為主）
> 不包含：Flet 畫面排版/元件優化
> 分析日期：2026-03-10

---

## 一句話結論

`app/` 真正的結構問題幾乎都集中在 `app/services.py`。

這支檔案已經變成 **service 神檔 / 廚房水槽檔**：
- 任務啟動
- logging 同步
- config 讀寫
- cache 管理
- 查詢工具
- QA/checkers
- ZIP merge
- preview

全部都塞在同一層，導致：
- 重複包裝邏輯很多
- service contract 不一致
- 啟動依賴很重
- 後續維護會越來越痛

`app/task_session.py` 本身不算壞，但目前抽象太薄、功能太少，還不足以真正撐住整個任務系統。

---

## 這次分析刻意避開的範圍

依照你的要求，**不分析 Flet UI 結構本身**。

所以這份報告：
- 不評論 layout
- 不評論按鈕/欄位配置
- 不評論 UI 元件拆分方式

但會分析：
- `views -> services` 的依賴邊界
- `services.py` 作為 service façade 的結構問題
- `TaskSession` 是否足夠支撐目前任務模型

---

# 一、app/ 目前的實際架構角色

## app/services.py
角色其實是：
1. UI 對 core 的 service façade
2. 任務執行器包裝層
3. logging / UI session 綁定點
4. config/rules 讀寫入口
5. cache UI 操作入口
6. lookup / QA / bundling / merge 的雜項集合

也就是說，它不是單純 service，而是：

> **Application layer + façade + task runner + utility gateway 的混合體**

這就是它變胖的根本原因。

## app/task_session.py
角色是：
- 單一長任務狀態容器
- 給 worker 寫入 progress/log/error
- 給 UI 讀 snapshot

概念是對的，但目前能力偏弱，只做了最基本的 thread-safe state box。

---

# 二、主要問題

## 1. `app/services.py` 已經是神檔

關鍵位置：
- `app/services.py:17` `class LogLimiter`
- `app/services.py:118` `def update_logger_config()`
- `app/services.py:156` `def run_lm_translation_service(...)`
- `app/services.py:226` `def run_lang_extraction_service(...)`
- `app/services.py:263` `def run_book_extraction_service(...)`
- `app/services.py:299` `def run_ftb_translation_service(...)`
- `app/services.py:342` `def run_kubejs_tooltip_service(...)`
- `app/services.py:383` `def run_md_translation_service(...)`
- `app/services.py:425` `def run_merge_zip_batch_service(...)`
- `app/services.py:664` `def cache_search_service(...)`
- `app/services.py:866` `def cache_rebuild_index_service()`
- `app/services.py:916` `def preview_jar_extraction_service(...)`

### 問題本質
這支檔案同時處理：
- 翻譯流程 service
- 抽取流程 service
- merge service
- QA service
- cache service
- config/rules service
- lookup service
- logging / session glue code

這不是單純「函數有點多」，而是**職責分類失敗**。

### 直接後果
1. 很難找到某個 domain 的邏輯邊界
2. 每次改 service 時都容易碰到其他功能
3. import 這個檔案會拉進一大堆 core dependency
4. 單元測試很難切小
5. 之後要拆 CLI / background worker / API 層都會卡住

### 建議拆分
建議至少拆成：

- `app/services/config_service.py`
  - `load_replace_rules`
  - `save_replace_rules`
  - `load_config_json`
  - `save_config_json`
  - `update_logger_config`

- `app/services/task_runner.py`
  - 共用的 session 啟動/收尾/錯誤包裝

- `app/services/translation_service.py`
  - `run_lm_translation_service`
  - `run_ftb_translation_service`
  - `run_kubejs_tooltip_service`
  - `run_md_translation_service`
  - `run_merge_zip_batch_service`

- `app/services/extraction_service.py`
  - `run_lang_extraction_service`
  - `run_book_extraction_service`
  - preview 類功能

- `app/services/cache_service.py`
  - `cache_*`

- `app/services/checker_service.py`
  - `run_untranslated_check_service`
  - `run_variant_compare_service`
  - `run_english_residue_check_service`
  - `run_variant_compare_tsv_service`

- `app/services/lookup_service.py`
  - `run_manual_lookup_service`
  - `run_batch_lookup_service`

這樣讀起來會乾淨很多。

---

## 2. 任務包裝邏輯大量重複

重複片段可見：
- `app/services.py:165-223`
- `app/services.py:228-261`
- `app/services.py:265-295`
- `app/services.py:311-340`
- `app/services.py:352-381`
- `app/services.py:394-423`
- `app/services.py:438-499`

這些函數反覆做同樣的事：
1. `update_logger_config()`
2. `session.start()`
3. `UI_LOG_HANDLER.set_session(session)`
4. 呼叫 generator 或 pipeline
5. 更新 progress/log
6. 例外處理
7. `session.finish()` / `session.set_error()`
8. `UI_LOG_HANDLER.set_session(None)`

### 問題
現在其實是把同一種 control flow 複製了很多遍，只是中間換不同核心函數。

### 這會造成
- 每個 service 的錯誤處理略有不同
- flush 時機不一致
- 有的手動 `session.add_log`，有的靠 logger，風格混雜
- 之後修一個 lifecycle bug，要改很多地方

### 建議
抽一個共用 helper，例如：

```python
def run_session_task(session, runner, *, with_log_filter=False, on_error_prefix=None):
    ...
```

或再分成兩類：
- `run_generator_task(...)`
- `run_callable_task(...)`

這樣 service 層會從「一堆幾乎同樣的函數」變成「薄 wrapper + 明確差異」。

---

## 3. service contract 不一致

目前 `app/services.py` 裡至少有三種風格：

### 類型 A：直接操作 session 的 task service
例如：
- `run_lm_translation_service`
- `run_ftb_translation_service`
- `run_kubejs_tooltip_service`
- `run_md_translation_service`

### 類型 B：yield update dict 的 generator service
例如：
- `run_batch_lookup_service`
- `run_bundling_service`
- `run_untranslated_check_service`
- `run_variant_compare_service`
- `run_english_residue_check_service`
- `run_variant_compare_tsv_service`

### 類型 C：直接 return dict / bool / str
例如：
- `cache_*`
- `run_manual_lookup_service`
- `preview_jar_extraction_service`

### 問題
這代表 `app/` 層沒有一致的 service 介面規格。

對維護者來說，讀 service 時每次都要先猜：
- 這個會 yield 嗎？
- 這個自己處理 session 嗎？
- 這個要不要 UI 自己包 progress？
- 這個錯誤是 raise 還是 return error dict？

### 建議
統一成兩層：

#### 內層
核心流程維持原本 generator / dict / callable 都可以。

#### 外層（app service）
統一只暴露兩種介面：
1. `run_xxx(session, ...) -> None`（task 型）
2. `query_xxx(...) -> result`（查詢型）

不要同一層混三四種模式。

---

## 4. `app/services.py` 同時管理 logging 與業務流程，耦合太重

關鍵位置：
- `app/services.py:17-68` `LogLimiter`
- `app/services.py:91-94` `UI_LOG_HANDLER`
- `app/services.py:118-153` `update_logger_config()`

### 問題
這裡把「業務流程 service」和「logging/session glue」綁得很死。

結果是：
- 只要你跑任務，就一定要順便想 UI_LOG_HANDLER
- service 層很難在非 UI 環境重用
- CLI / 測試 / 批次腳本環境會變得很不乾淨

### 建議
把 logging/session glue 往 `task_runner.py` 或 `app/runtime/` 類型模組收斂。

service 應該比較像：
- 呼叫核心流程
- 回傳標準化結果

而不是每個 service 都自己手動操作 logging handler。

---

## 5. `CONFIG_PATH` / `REPLACE_RULES_PATH` 綁死 `os.getcwd()`，有執行目錄耦合

位置：
- `app/services.py:98`
- `app/services.py:99`

```python
CONFIG_PATH = os.path.join(os.getcwd(), "config.json")
REPLACE_RULES_PATH = os.path.join(os.getcwd(), "replace_rules.json")
```

### 問題
這代表設定檔位置取決於「你從哪個 cwd 啟動」。

這在以下情境會出事：
- 從別的資料夾呼叫程式
- 測試環境切換 cwd
- 未來做 CLI / 打包 / shortcut 啟動時，工作目錄不一致

### 建議
改成以專案根路徑或檔案自身位置為準，例如：

```python
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.json"
```

這是很標準、很值得先修的問題。

---

## 6. `preview_jar_extraction_service()` 看起來是壞引用 / 死 service 候選

位置：
- `app/services.py:916`
- `app/services.py:933`
- `app/services.py:936`

它裡面做的是：

```python
from translation_tool.core.jar_processor import preview_extraction
result = preview_extraction(mods_dir, mode)
```

但實際在 `jar_processor.py` 看到的是：
- `translation_tool/core/jar_processor.py:313` `def preview_extraction_generator(...)`

也就是說：
- service 這裡引用的 `preview_extraction` 不存在
- `app/views/extractor_view.py:10` 有 import 這個 service
- 但實際 `extractor_view.py` 裡真正使用的是 `preview_extraction_generator`

### 結論
這支 service 高機率是：
- 舊接口殘留
- 未完成重構後留下來的壞引用
- 或根本沒人在用

### 建議
先確認後處理：
1. 若真的沒在用 → 刪掉
2. 若還想保留 service 入口 → 改成正確包裝 `preview_extraction_generator`

這是很明顯的清理目標。

---

## 7. `TaskSession` 概念對，但抽象太薄

位置：
- `app/task_session.py:7` `class TaskSession`
- `app/task_session.py:19` `_last_log_flush = 0.0`

### 優點
- thread-safe
- 單一任務狀態來源（SSOT）
- `snapshot()` 設計合理

### 問題
#### (1) `_last_log_flush` 沒被使用
只看到定義：
- `app/task_session.py:19`

沒有其他引用。

這是典型殘留欄位，建議直接刪。

#### (2) `TaskSession` 太被動
它現在只是個 state box，沒有提供：
- 統一 task lifecycle context
- log flush 規則
- status message
- warning / cancellation / partial success

結果是很多 lifecycle 邏輯被迫留在 `services.py`。

### 建議
`TaskSession` 之後可以往兩個方向選一個：

#### 路線 A：維持極簡
那就把沒用欄位刪掉，承認它只是 state DTO。

#### 路線 B：升級成真正 task runtime object
讓它承接：
- `begin()` / `complete()` / `fail()`
- `append_logs()`
- 狀態訊息
- flush hook

我傾向 **路線 A 先做**，因為目前更急的是把 `services.py` 瘦身。

---

## 8. `views -> services` 的邊界目前太薄，造成 service 被迫知道太多 UI 細節

這次不分析 UI 版面，但從 import 關係看得出來：
- 多數 view 幾乎直接依賴 `app.services`
- `services.py` 因而變成所有 UI 操作的唯一總入口

這個做法短期很方便，但副作用就是：
- service 不再是 domain-oriented，而是 UI-driven
- 哪個按鈕需要什麼，就往 services.py 塞一個對應函數
- 最後 service 層的切法就會跟 UI 頁籤長得一模一樣

### 建議
之後可以保留 UI 不動，但讓 service 改成 domain-oriented：
- translation
- extraction
- cache
- checker
- lookup

view 只是去調 domain service，而不是逼 service 層變成「所有按鈕的 API 集合」。

---

# 三、可刪除 / 可疑殘留項目

## 可刪除或優先確認

### 1. `app/task_session.py:19`
- `_last_log_flush = 0.0`
- 目前未使用
- 可刪

### 2. `app/services.py:916-936`
- `preview_jar_extraction_service()`
- 內部引用不存在的 `preview_extraction`
- 高機率舊碼 / 壞引用 / 無用 wrapper

### 3. `app/services.py` 內大量重複的 task wrapper 結構
- 不是「刪掉函數」
- 而是應該抽象化合併

---

# 四、重複邏輯

## 1. 任務 lifecycle 重複
重複區段：
- `run_lm_translation_service`
- `run_lang_extraction_service`
- `run_book_extraction_service`
- `run_ftb_translation_service`
- `run_kubejs_tooltip_service`
- `run_md_translation_service`
- `run_merge_zip_batch_service`

共同模式：
- logger config
- session start
- 綁 UI handler
- 執行流程
- 轉發 progress/log/error
- finish / error
- 清 handler

這是最值得抽出共用 runner 的地方。

## 2. generator 過濾 / flush 重複
重複區段：
- `GLOBAL_LOG_LIMITER.filter(update_dict)`
- `GLOBAL_LOG_LIMITER.flush()`

這一層可以抽到統一 helper，例如：
- `consume_generator_updates(...)`

## 3. config / rules 薄 wrapper 重複
- `load_replace_rules`
- `save_replace_rules`
- `load_config_json`
- `save_config_json`

這些其實適合移到 `config_service.py`，避免 service 神檔再膨脹。

---

# 五、過度拆分 / 拆得不夠好的地方

這個 app 層比較特別，不是「拆太細」，而是：

> **上層檔案沒拆，底層責任卻又混太多。**

也就是說，問題不是碎片化，而是「應該分模組的沒分」。

### 真正該做的不是再拆更多小函數
而是：
- 先把 `services.py` 依 domain 分檔
- 再把重複 task wrapper 合併

如果現在繼續在 `services.py` 裡增加更多 helper 小函數，只會讓它變成：
- 還是同一支神檔
- 但裡面多更多跳來跳去的小函數
- 更難讀

所以這裡我的建議很明確：

> **先分模組，再談函數細拆。**

---

# 六、建議重構順序（只針對 app/）

## 第一優先
### 把 `services.py` 依 domain 拆檔
這是最大收益點。

## 第二優先
### 抽出共用 task runner
把重複的：
- session lifecycle
- UI logging handler 綁定
- generator 消費
- error handling

統一管理。

## 第三優先
### 修正 `preview_jar_extraction_service()`
因為這個已經接近壞引用。

## 第四優先
### 修掉 `os.getcwd()` 路徑耦合
這種問題現在不一定炸，但將來很容易炸。

## 第五優先
### 清理 `TaskSession` 殘留欄位
小問題，但順手該做。

---

# 七、最後結論

`app/` 的真正問題，不在 UI，而在 **application service layer 沒有長成乾淨的層次**。

目前狀態是：
- `TaskSession` 太薄
- `services.py` 太胖
- service contract 不一致
- domain 邊界不清楚

所以這一層最需要的不是「多拆幾個函數」，而是：

1. **先分模組**
2. **再統一任務執行模型**
3. **最後才做細節清理**

這樣你可以在**不碰 Flet UI 結構**的前提下，把 app 層可讀性拉高很多，而且風險相對低。

---

## 本檔建議狀態

- **可直接討論**：是
- **建議先動哪裡**：`app/services.py`
- **是否需要碰 UI**：不用
- **風險**：低到中（只要先做 service 分層，不改畫面結構，風險可控）
