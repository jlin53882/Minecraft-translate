# Logging System Unification — Master Plan（3 PR 拆分總覽）

> 日期：2026-03-20 18:05  
> 專案：`minecraft_translator_flet`  
> 目標：把長任務 UI log 系統從「各 view 各自維護」收斂成可配置、可驗證、可長期維護的統一架構，避免未來持續 patch。

---

## Summary

本規劃採 **情況 C（完整收斂）**，但不做一顆超大 PR，而是拆成 **3 顆可驗證、可回退、風險可控的 PR**：

- **PR1：Logging Core Foundation**
  - 建立新的 logging SSOT（資料模型、config、presenter、同步邏輯）
  - 先不大規模切換所有 caller

- **PR2：Merge / Extractor 接線**
  - 將最容易卡頓的長任務頁面接到新架構
  - 驗證 freeze / memory / cursor / modal 互動

- **PR3：Translation / Secondary Views 收斂 + 舊邏輯清理**
  - 把剩餘主要長任務頁接入新架構
  - 清除舊 patch / duplicated logic / obsolete helpers

這樣做的原因：
1. `TaskSession` 與 UI rendering 會一起動，不能一口氣全專案硬改
2. config 契約會新增，必須先定 SSOT，再接線 caller
3. 每顆 PR 都要有自己的 validation checklist，避免「改到一半不知道哪裡壞」

---

## 為什麼這次要動 `TaskSession`

前一輪方案 B 是「先不動 TaskSession，先抽 UI presenter」。
但家豪這次已明確要求：

- 不想再一直 patch
- config 會控制 info / warning / error 等顯示規則
- 想把 log 機制視為一個系統，而不是一堆 view 各自補丁

在這個前提下，如果只做 presenter，不升級 `TaskSession`：
- presenter 仍只能拿到 `list[str]`
- 無法穩定支援 log level / source / seq / filtering
- config 只能做很淺的 UI 顏色規則，做不到真正的 log 策略治理

因此本次設計改採：

> **`TaskSession` 從「字串佇列」升級為「LogEntry 事件佇列」**

這是情況 C 的必要條件。

---

## 新架構概念（SSOT）

### 建議新資料夾

```text
app/logging/
├─ __init__.py
├─ log_entry.py          # LogEntry / LogLevel 型別
├─ task_session.py       # 新版 TaskSession（支援 LogEntry）
├─ log_presenter.py      # UI log 同步與渲染策略
├─ log_config.py         # config 讀取 / normalize
├─ log_colors.py         # level -> color 規則
└─ log_sync.py           # optional：poller 共用 helper（若需要）
```

---

## 新資料模型（方向）

### `LogEntry`

```python
@dataclass(frozen=True)
class LogEntry:
    seq: int
    level: str      # info / warning / error / system / debug
    text: str
    source: str     # merge / extractor / translation / bundler ...
    ts: float       # time.time()
```

### 新版 `TaskSession`
- `logs: deque[LogEntry]`
- `next_seq: int`
- `add_log(text, level="info", source="ui")`
- `snapshot()` 回傳結構化資料

### `LogPresenter`
支援兩種模式：
- `append`：適合 merge / extractor
- `tail`：適合 translation

### `log_config`
從 config 統一讀取：
- max session logs
- max ui lines
- tail lines
- enabled levels
- colorize
- source filter（未來可擴）

---

## 三顆 PR 的責任切分

| PR | 目標 | 動到哪一層 | 風險 |
|---|---|---|---|
| PR1 | 建立 logging core foundation | 底層 + 共用層 | 中 |
| PR2 | 接線 merge / extractor | 高風險長任務 view | 中 |
| PR3 | 收斂 translation / secondary views + cleanup | view 層 + 舊邏輯清理 | 中 |

---

## Phase 0：共通驗證原則

所有 3 顆 PR 都必須遵守：

1. 不得捏造 freeze 解決結果，必須有實際 validation output
2. 不得一次把所有 view 全改完又無法分辨是哪裡壞
3. 任何 config 契約新增，都要有 default / backward compatibility 說明
4. 每顆 PR 都要補對應測試，不接受只靠手動說「應該可以」

---

## 3 顆 PR 對應檔案

### 1. 總覽（本檔）
- `docs/pr/2026-03-20_1805_PR_logging-system-unification_master-plan.md`

### 2. PR1 設計稿
- `docs/pr/2026-03-20_1806_PR_logging-core-foundation-design.md`

### 3. PR2 設計稿
- `docs/pr/2026-03-20_1807_PR_logging-merge-extractor-integration-design.md`

### 4. PR3 設計稿
- `docs/pr/2026-03-20_1808_PR_logging-translation-cleanup-design.md`

---

## Rejected approaches
- 試過：只繼續用方案 A / B，在各 view 補 UI controls 上限與 `_last_log_count` 修補
- 為什麼放棄：這會讓 log level / config / source / rendering 策略繼續分散，未來仍會回到 patch 模式
- 最終改採：情況 C，升級 `TaskSession` + 抽共用 logging 資料夾與 presenter，再分 3 顆 PR 漸進接線

---

## Not included in this PR
- 沒有直接實作 code
- 沒有直接修改任何 view
- 沒有一次列出所有 secondary views 的完整收斂清單（放在各 PR 細稿）

---

## Next step

1. 依 PR1 設計稿先建立 logging core foundation
2. PR1 完成後再接 PR2（merge / extractor）
3. PR2 穩定後再做 PR3（translation / cleanup）

---

*本檔為總覽與拆分決策；具體設計細節請看 3 份分 PR 設計稿。*
