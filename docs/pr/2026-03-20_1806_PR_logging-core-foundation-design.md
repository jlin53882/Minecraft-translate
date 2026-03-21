# PR1 設計稿：Logging Core Foundation

> 日期：2026-03-20 18:06  
> 專案：`minecraft_translator_flet`  
> 目標：建立新的 logging SSOT，讓後續 view 接線不再依賴 `list[str] + _last_log_count` 的舊模式。

---

## Summary

PR1 只做**底層與共用層**，不大規模改各頁面行為。  
重點是先把新的 logging 基礎打好，包括：

- `app/logging/` 共用資料夾
- `LogEntry` 資料模型
- 新版 `TaskSession`
- `log_config` 規範
- `LogPresenter`（append / tail 兩模式）
- backward compatibility 過渡層

這顆 PR 的成功標準不是「所有 view 都切完」，而是：

> **新 logging core 已可被 import、可被測、可被舊 caller 漸進接線。**

---

## 現況分析（已驗證）

### 1. `TaskSession` 目前只存 `str`
驗證：`app/task_session.py`
- `logs = deque(maxlen=max_logs)`
- `add_log(text: str)`
- `snapshot()["logs"] -> list[str]`

### 2. 各 view 對 log 的假設不一致
驗證：
- `app/views/merge_view.py`
- `app/views/translation/translation_actions.py`
- `app/views/extractor/extractor_actions.py`

目前存在三種不同假設：
- append + length cursor
- tail rebuild
- append + custom color/stats side effects

### 3. config 尚無 logging SSOT
目前沒有單一 config 區塊明確定義：
- session log 上限
- UI tail 顯示上限
- level 顯示規則
- colorize / filter / source 規則

---

## 設計目標

### 本次要做
1. 新增 `app/logging/` 資料夾
2. 定義 `LogEntry`
3. 升級 `TaskSession` 以支援結構化 log
4. 建立 `LogPresenter`
5. 建立 `log_config` 與 default normalization
6. 保留相容層，避免 PR1 就炸掉所有舊 caller

### 本次不做
- 不把 merge / extractor / translation 全部切換完
- 不清理所有舊 `_last_log_count` 邏輯
- 不做 full-featured log filtering UI
- 不動 pipeline 本體的 business logic

---

## 新資料夾結構

```text
app/logging/
├─ __init__.py
├─ log_entry.py
├─ task_session.py
├─ log_presenter.py
├─ log_config.py
└─ log_colors.py
```

---

## 核心設計

## 1. `log_entry.py`

### 提案
```python
@dataclass(frozen=True)
class LogEntry:
    seq: int
    level: str
    text: str
    source: str
    ts: float
```

### level 建議值
- `debug`
- `info`
- `warning`
- `error`
- `system`

### 為什麼要 `seq`
因為這次情況 C 的核心之一，就是不要再用單純 `len(logs)` 當 cursor。  
有穩定 `seq` 後：
- presenter 可用 `last_seq`
- deque 滑動不會讓 UI cursor 誤判「哪些是新 log」

---

## 2. 新版 `TaskSession`

### 提案責任
- `progress`
- `status`
- `error`
- `logs: deque[LogEntry]`
- `next_seq`

### API 提案
```python
def add_log(self, text: str, level: str = "info", source: str = "ui") -> None
```

### backward compatibility
為避免 PR1 直接炸掉所有 caller：
- `add_log("xxx")` 仍合法
- 若未傳 `level/source`，自動用 default
- `snapshot()` 除了 `logs` 外，可額外提供：
  - `log_texts`（暫時相容舊 caller）
  或
  - 讓 `LogPresenter` 直接吃 `logs`（LogEntry list），舊 view 暫不切換

### 我建議
PR1 先保留：
```python
snapshot()["logs"] = list[LogEntry]
```
再由 PR2/PR3 負責 caller 遷移。不要同時維護兩套 snapshot key 太久。

---

## 3. `log_config.py`

### config 提案
```json
{
  "ui_logging": {
    "max_session_logs": 2000,
    "max_ui_lines": 300,
    "tail_lines": 250,
    "show_levels": ["system", "info", "warning", "error"],
    "colorize": true
  }
}
```

### default 策略
- 若 config 缺少 `ui_logging`，提供安全 default
- 不因缺 config 讓舊專案直接壞掉

### 為什麼 PR1 就要加 config
因為家豪已明確要求：
- info / error 等顯示規則可調

這屬於 system contract，不能等到 view 接線時才邊做邊猜。

---

## 4. `log_presenter.py`

### 兩種模式

#### A. append mode
- 用 `last_seq` 同步新行
- 適合 merge / extractor

#### B. tail mode
- 直接取最後 `tail_lines`
- 適合 translation

### 主要 API
```python
class LogPresenter:
    def reset(self): ...
    def sync(self, list_view, entries: list[LogEntry]): ...
```

### 必要能力
- level filter
- 顏色映射
- max UI lines
- 只在有新內容時 scroll

---

## 5. `log_colors.py`

### 建議責任
把各 view 現在散落的：
- ERROR 紅
- 系統 綠
- 完成 藍

收斂成共用 helper。

### 好處
- 不同 view 不再各自判斷字串內容
- 未來若 config 要控制顏色，也有固定入口

---

## 遷移策略

### PR1 只做 foundation，不強制所有 caller 全切
可接受狀態：
- 新 logging core 存在
- 測試通過
- 舊 `app/task_session.py` 可以保留薄 wrapper / re-export
- view 暫時仍可跑舊邏輯

### 建議
`app/task_session.py` 在 PR1 階段可改成：
- re-export 新版 `TaskSession`
- 避免所有 import 先大爆炸

---

## Validation checklist
- [ ] `python -m py_compile app/logging/log_entry.py app/logging/task_session.py app/logging/log_presenter.py app/logging/log_config.py app/logging/log_colors.py app/task_session.py`
- [ ] `uv run pytest -q tests/test_task_session.py`
- [ ] `uv run pytest -q tests/test_log_presenter.py`
- [ ] `uv run python -c "from app.logging.task_session import TaskSession; s=TaskSession(); s.add_log('ok'); print('import-ok')"`
- [ ] `uv run python -c "from app.logging.log_presenter import LogPresenter; print('presenter-ok')"`

---

## 測試設計

### 建議新增
- `tests/test_log_presenter.py`
- `tests/test_logging_config.py`

### 必測案例
1. `TaskSession.add_log()` 會自動產生遞增 `seq`
2. `deque(maxlen=...)` 滑動後仍保留正確最後 N 筆
3. `LogPresenter` append mode 不重複渲染同 seq
4. `LogPresenter` tail mode 只保留最後 N 筆
5. config 缺少欄位時能正常 fallback

---

## Phase 1 完成清單（預計）
- [ ] 做了：新增 `app/logging/` 資料夾
- [ ] 做了：定義 `LogEntry`
- [ ] 做了：升級 `TaskSession`
- [ ] 做了：新增 `LogPresenter`
- [ ] 做了：新增 logging config normalization
- [ ] 做了：`app/task_session.py` 相容層
- [ ] 未做：view caller 大規模切換（留到 PR2/PR3）

---

## Important findings

1. 若 PR1 就硬切所有 caller，風險太高。  
2. 只要 foundation 定義清楚，PR2/PR3 的工作會單純很多。  
3. `seq` 是這次情況 C 的核心價值，不能省略。  
4. config 契約要先立，否則後面每顆 PR 都會對 logging 規則各說各話。

---

## Rejected approaches
- 試過：PR1 只做 `LogPresenter`，不動 `TaskSession`
- 為什麼放棄：家豪已明確要求支援 config 的 info / error 等規則；若底層仍是 `list[str]`，無法穩定支援 level/source/filter/seq
- 最終改採：PR1 先把 `TaskSession` 升級成結構化 log container，再讓 presenter 建立在新模型上

---

## Not included in this PR
- 沒有切換 merge / extractor / translation caller
- 沒有刪掉所有舊 log view helper
- 沒有實作 UI filter 控件

---

## Next step
PR1 完成後，進入 PR2：
- 先接 `merge_view`
- 再接 `extractor`
- 驗證最容易 freeze 的頁面先穩住
