# PR2 設計稿：Logging Merge / Extractor Integration

> 日期：2026-03-20 18:07  
> 專案：`minecraft_translator_flet`  
> 目標：將最容易 freeze 的長任務頁面（merge / extractor）接到 PR1 建立的新 logging core，驗證 append mode 與高頻 log 任務下的穩定性。

---

## Summary

PR2 專注在**高風險長任務頁面接線**：
- `merge_view.py`
- `extractor_view.py` / `extractor_actions.py`

這兩個頁面都具有：
- 背景執行緒持續寫 log
- UI poller 高頻同步
- 先前已觀察到 freeze / summary / cursor 問題

因此它們最適合作為新 logging core 的第一批 caller。

---

## 現況分析（已驗證）

### A. Merge View
驗證：`app/views/merge_view.py`
- 使用 `_last_log_count`
- 每 100ms poll 一次 `session.snapshot()`
- 逐行 append `ft.Text(...)`
- PR25 已補 UI上限，但仍是 view 自己管理 log 渲染

### B. Extractor
驗證：`app/views/extractor/extractor_actions.py`
- 使用 `_last_rendered_log_count`
- 逐行 append `view._append_log_line(line)`
- 還有 stats side effect（`update_stats_from_log(view, line)`）
- 之前已踩過 modal / summary / log flush 類問題

### 結論
這兩個頁面共同證明：
- 現在需要共用 append-mode log presenter
- caller 不該再自己維護 cursor

---

## 設計目標

### 本次要做
1. `merge_view` 改用 `LogPresenter(mode="append")`
2. `extractor` 改用 `LogPresenter(mode="append")`
3. 移除 view/action 內重複的 `_last_log_count` / `_last_rendered_log_count` 管理
4. 確保 extractor 的 stats side effect 不被 presenter 化破壞
5. 驗證 UI 不 freeze、不重複、不漏 log

### 本次不做
- 不處理 translation（留 PR3）
- 不處理所有 secondary views
- 不做完整 UI filter 控件

---

## Merge View 接線方案

### 現在問題
- view 內直接 append controls
- truncate / scroll / cursor 都由 view 自己管
- 再次擴散 patch 風險高

### 改法
- 在 `MergeView.__init__()` 建立：
  ```python
  self.log_presenter = LogPresenter(mode="append", ...)
  ```
- `_start_ui_poller()` 裡改成：
  ```python
  self.log_presenter.sync(self.log_view, snap["logs"])
  ```
- 移除 `_last_log_count` 手工邏輯

### 保持不變
- `progress_bar`
- `status_chip`
- `start_button` disable/enable
- merge service 本體

---

## Extractor 接線方案

### 目前特殊點
Extractor 不只顯示 log，還有：
- `update_stats_from_log(view, line)`
- summary dialog
- modal 互動

### 風險
如果直接把 log append 改掉，卻忘記 stats side effect：
- UI log 會正常
- 但 summary 數據可能壞掉

### 改法
有兩種可行做法：

#### 方案 1（建議）
`LogPresenter.sync()` 回傳「本輪新增 entries」

例如：
```python
new_entries = view.log_presenter.sync(view.log_view, snap["logs"])
for entry in new_entries:
    update_stats_from_log(view, entry.text)
```

### 為什麼建議這樣做
- presenter 專心負責 UI rendering
- stats 邏輯仍由 extractor 端控制
- 不把 extractor-specific side effect 塞進共用 presenter

#### 方案 2（不建議）
讓 presenter 接 callback 並在內部調 stats

### 為什麼不建議
- 會讓 presenter 綁死某些 caller 行為
- 共用層責任變髒

### 最終改採
- presenter 回傳 `new_entries`
- extractor 端自己跑 stats side effect

---

## UI 行為契約

PR2 對 merge / extractor 的共通契約：

1. Presenter append mode 只渲染新 seq 的 entries
2. UI controls 數量受 config / presenter 控制
3. 有新增行時才 scroll
4. status/progress 控件行為與現在一致
5. freeze 風險不再由各 view 自己 patch

---

## 驗證重點

### Merge 要驗證
- 大量 log 下 controls 不膨脹到 freeze
- 不重複渲染
- 不漏最後幾行

### Extractor 要驗證
- log panel 正常更新
- `update_stats_from_log()` 仍正確觸發
- 完成摘要 dialog 的數字與 session log 一致
- modal 開關不因 presenter 接線受影響

---

## Validation checklist
- [ ] `python -m py_compile app/views/merge_view.py app/views/extractor_view.py app/views/extractor/extractor_actions.py`
- [ ] `uv run pytest -q tests/test_merge_view_characterization.py`
- [ ] `uv run pytest -q tests/test_extractor_view_characterization.py`
- [ ] `uv run pytest -q tests/test_log_presenter.py`
- [ ] 手動驗證：Merge 長任務 2000+ log 時 UI 不中斷
- [ ] 手動驗證：Extractor Lang / Book 都能正常更新 log，summary dialog 可正常顯示與關閉

---

## 建議補測

### `tests/test_merge_log_presenter_integration.py`
至少驗證：
- presenter sync 後，舊 `_last_log_count` 不再是必要依賴
- UI controls 不會無限增長

### `tests/test_extractor_log_presenter_integration.py`
至少驗證：
- presenter 回傳的新 entries 能正確驅動 stats
- summary 數字不因 presenter 化而錯亂

---

## Phase 1 完成清單（預計）
- [ ] 做了：`merge_view` 接到 `LogPresenter`
- [ ] 做了：`extractor` 接到 `LogPresenter`
- [ ] 做了：extractor stats side effect 改為吃 `new_entries`
- [ ] 做了：清掉 merge/extractor 舊 cursor patch
- [ ] 未做：translation / secondary views（留 PR3）

---

## Important findings

1. Merge 與 Extractor 都屬 append-heavy 長任務頁，應一起驗證 append mode。  
2. Extractor 的 stats side effect 是接線時最容易被漏掉的行為。  
3. PR2 若穩，代表新 logging core 已可撐住最容易 freeze 的高風險場景。

---

## Rejected approaches
- 試過：PR2 只先接 merge，extractor 留到 PR3
- 為什麼放棄：extractor 也是高風險長任務頁，而且已有 log / summary / modal 相關問題；若不一起驗證 append mode，PR2 的信心不夠
- 最終改採：PR2 一次處理 merge + extractor，translation tail mode 留到 PR3

---

## Not included in this PR
- 沒有接 translation tail mode
- 沒有處理 bundler / qc
- 沒有做 UI filter panel

---

## Next step
PR2 穩定後，進入 PR3：
- 接 translation
- 清理舊 helper / duplicated logic
- 視情況收 secondary views
