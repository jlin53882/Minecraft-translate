# PR3 設計稿：Logging Translation / Cleanup Convergence

> 日期：2026-03-20 18:08  
> 專案：`minecraft_translator_flet`  
> 目標：將 Translation 等剩餘主要長任務頁收斂到新 logging system，並清理舊有 duplicated logic / compatibility patches。

---

## Summary

PR3 是整個情況 C 的收尾 PR。  
在 PR1 建立 foundation、PR2 驗證高風險 append-mode 場景之後，PR3 要完成：

1. `translation` 接入新 logging system（tail mode）
2. 視範圍納入 secondary views（如 `bundler` / `qc`）
3. 清掉舊 `_last_log_count` 類 patch 與 duplicated logic
4. 收斂 import / wrapper / compatibility shim

這顆 PR 的成功標準是：

> **主要長任務 UI log 已全部收斂到同一套 logging architecture，舊補丁不再散落。**

---

## 現況分析（已驗證）

### Translation
驗證：`app/views/translation/translation_actions.py`
- 使用 `tail = logs[-250:]`
- 每輪重建 `view.log_view.controls = [...]`
- 沒有 `_last_log_count` 問題，但有自己的 tail 策略

### Secondary Views（待盤點納入）
從原始碼搜尋可知，以下 view 也有 `log_view.controls.append(...)` 型態：
- `bundler_view.py`
- `qc_view.py`
- 可能還有其他輕量頁

### 結論
PR3 的角色不是再驗證 append freeze，而是：
- 驗證 tail mode
- 清理剩餘不一致邏輯
- 讓整個 logging system 真正完成收斂

---

## 設計目標

### 本次要做
1. `translation` 切到 `LogPresenter(mode="tail")`
2. 將 translation 的 tail 大小改由 config / presenter 控制
3. 清理 translation 舊的 log rebuild 邏輯
4. 視範圍把 `bundler / qc` 等 secondary views 納入同一套 presenter
5. 刪除過時 compatibility helpers / legacy patches

### 本次不做
- 不再改 `TaskSession` core model
- 不新增更高階 UI filter/search 功能
- 不做 log export / search panel

---

## Translation 接線方案

### 現在策略
- 每輪取 `logs[-250:]`
- 直接整批重建 controls

### 新策略
使用：
```python
LogPresenter(mode="tail", tail_lines=<from config>)
```

### 為什麼這樣做
- 保留 translation 原本「只顯示最後 N 行」的使用習慣
- 不強迫 translation 變成 append mode
- 讓 tail 規則從 view 內硬編碼移到 presenter/config

---

## Secondary Views 納入原則

### 可以納入的條件
- 有明確長任務 log panel
- 現在也有 controls 逐行累積風險
- 接入 presenter 後不會明顯增加 PR 風險

### 不該硬納入的條件
- 該頁不是長任務主場景
- 目前沒有 freeze / memory evidence
- 接入會導致 PR3 範圍膨脹過大

### 建議
PR3 至少完成：
- `translation`

`bundler / qc` 視時間與驗證情況列為：
- 可納入
- 或留小顆 follow-up PR

---

## Cleanup 範圍

### 1. 移除舊 cursor patch
例如：
- `_last_log_count`
- `_last_rendered_log_count`
- 只剩 presenter 管理 cursor / tail

### 2. 移除 duplicated colorize / append logic
把散在 view 裡的：
- line color 判斷
- truncate controls
- scroll

統一收斂。

### 3. 清理 import surface
如果 `app/task_session.py` 還是 compatibility shim，PR3 要決定：
- 保留 re-export
- 或正式把 caller 都改到 `app.logging.task_session`

我建議：
- 若 caller 已全部切換完成，可在 PR3 清理 import surface
- 若外部引用仍多，保留薄 wrapper，避免破壞性清理過早發生

---

## 驗證重點

### Translation
- tail mode 仍維持近 250 行體驗（或 config 指定值）
- 不因 presenter 化造成卡頓或 log 消失
- status / progress 顯示不變

### Cleanup
- 舊 `_last_log_count` 類 patch 不再被任何主要長任務頁依賴
- secondary views 若已納入，行為與原本一致

---

## Validation checklist
- [ ] `python -m py_compile app/views/translation/translation_actions.py app/views/translation_view.py`
- [ ] `uv run pytest -q tests/test_translation_view_characterization.py`
- [ ] `uv run pytest -q tests/test_log_presenter.py`
- [ ] `uv run pytest -q tests/test_merge_view_characterization.py tests/test_extractor_view_characterization.py tests/test_translation_view_characterization.py`
- [ ] 手動驗證：translation 長任務仍只顯示 tail，不會卡頓
- [ ] 手動驗證：merge / extractor / translation 三者 log 行為一致且可預測

---

## 建議補測

### `tests/test_translation_log_presenter_integration.py`
至少驗證：
- tail mode 下只顯示最後 N 筆
- config 改 tail_lines 時，UI 顯示筆數同步改變

### `tests/test_logging_config.py`
確認：
- `ui_logging.tail_lines`
- `ui_logging.max_ui_lines`
- `ui_logging.show_levels`
能被正常讀取並 fallback

---

## Phase 1 完成清單（預計）
- [ ] 做了：translation 接到 `LogPresenter(mode="tail")`
- [ ] 做了：tail 顯示筆數改由 config 控制
- [ ] 做了：清理 translation 舊 log rebuild 邏輯
- [ ] 做了：清理主要長任務頁殘留 legacy cursor patch
- [ ] 視情況做了：secondary views 納入 presenter
- [ ] 超出範圍：更高階 log filter/search/export UI

---

## Important findings

1. Translation 的核心不是 freeze，而是「tail 策略要保留但改成共用層實作」。  
2. PR3 成功與否，關鍵在於 cleanup 不要過度擴大範圍。  
3. cleanup 若貪心一次收太多 secondary views，容易讓 PR3 爆範圍。

---

## Rejected approaches
- 試過：PR3 一次把所有有 log_view 的 view 全部收斂完
- 為什麼放棄：範圍過大，會讓 cleanup 與架構驗證糾纏在一起；失敗時難以定位是哪一個 caller 壞掉
- 最終改採：PR3 先保證 translation 收斂完成，secondary views 視風險逐步納入

---

## Not included in this PR
- 沒有做 log 搜尋 / 匯出 / 過濾 UI
- 沒有做更高階 analytics dashboard
- 沒有動 pipeline business logic

---

## Next step
PR3 完成後：
1. logging system 的主要長任務頁面已完成收斂
2. 若 secondary views 仍有零星差異，再開小顆 cleanup PR
3. 若未來需要更高階 filtering/search，再基於本次新 core 擴充
