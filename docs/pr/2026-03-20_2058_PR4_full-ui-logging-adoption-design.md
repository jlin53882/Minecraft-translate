# PR4 設計稿：Full UI Logging Adoption / Cleanup

> 日期：2026-03-20 20:58  
> 專案：`minecraft_translator_flet`  
> 目標：把全專案真正屬於 **UI log stream** 的調用點，全面收斂到新 logging layer；同時明確排除 SnackBar / dialog / 單次提示等不屬於 log stream 的 UI 訊息，避免未來再次出現局部補丁。

---

## Summary

PR1–PR3 已完成三條主要長任務主線的收斂：
- Merge
- Extractor
- Translation

但目前專案內仍存在多種「UI log / UI message」形式混用：
- session-driven 長任務 log panel
- 單次本地 log append
- SnackBar / dialog / toast 類提示
- 非主線 view 的 log panel 或 pseudo-log panel

如果停在 PR3，雖然主幹穩了，但未來仍可能出現：
- 新功能接舊邏輯
- 某些 view 還是自己 append controls
- 某些頁面又因 controls 膨脹而補丁式修補

因此 PR4 的目的不是「把所有 UI 訊息都塞進 logging system」，而是：

> **把所有真正屬於 UI log stream 的場景全面 adoption 到新 logging layer，並把不屬於 log stream 的 UI 提示正式分類為例外。**

---

## 核心決策

### 這次要收斂的是什麼？
收斂對象：
- 有 log panel / log list / 持續追加訊息的長任務或半長任務 UI
- 會因持續 append controls 而造成 freeze / 膨脹風險的地方

### 這次不強迫收的是什麼？
不收斂到 logging layer 的對象：
- SnackBar
- AlertDialog / Modal 單次錯誤訊息
- 表單驗證失敗提示
- 純 local、非串流型的 UI feedback

### 為什麼要這樣分
因為「UI 訊息」不等於「log stream」。
若把 snack / dialog 也硬塞進 `TaskSession + LogPresenter`，只會把架構搞混。

---

## Phase 0：盤點與分類（已做初步驗證）

### 已完成收斂（PR1–PR3）
| 類別 | 檔案 | 現況 |
|---|---|---|
| 長任務 log stream | `app/views/merge_view.py` | ✅ 已接 LogPresenter append |
| 長任務 log stream | `app/views/extractor/extractor_actions.py` | ✅ 已接 LogPresenter append |
| 長任務 log stream | `app/views/translation/translation_actions.py` | ✅ 已接 LogPresenter tail |

---

### 尚未收斂、但屬於 **候選 UI log stream**
| 類別 | 檔案 | 現況 | 判定 |
|---|---|---|---|
| 長任務 / pseudo-task log | `app/views/bundler_view.py` | 直接 `log_view.controls.append(ft.Text(...))` | **應收斂** |
| 長任務 / pseudo-task log | `app/views/lm_view.py` | poller 內 append `log_view.controls` | **應收斂** |
| QC log panel | `app/views/qc_base.py` | 直接 append controls | **應收斂** |
| QC view | `app/views/qc_view.py` | 直接 append controls | **應收斂** |

---

### 明確屬於 **不納入 logging layer** 的 UI 提示
| 類別 | 檔案範例 | 現況 | 判定 |
|---|---|---|---|
| SnackBar | `merge_view.py`, `extractor_view.py`, `translation_view.py`, `rules_view.py` 等 | `_show_snack_bar()` / `_show_snack()` | **不收斂到 TaskSession/Presenter** |
| dialog 錯誤提示 | `extractor_view.py`, `extractor_actions.py` | 預覽/錯誤 dialog | **不收斂到 TaskSession/Presenter** |
| 表單/互動提示 | `config_view.py`, `rules_view.py`, `cache_*` | 局部 UI feedback | **不收斂到 TaskSession/Presenter** |

---

## 設計目標

### 本次要做
1. 找出所有「真正屬於 UI log stream」的剩餘 view
2. 將這些 view 全部接到 `LogPresenter`
3. 明確標記哪些 helper / snack / dialog 不屬於 logging system
4. 清掉殘留的局部 truncate / append / cursor patch
5. 建立「之後新 view 要遵守什麼規則」的統一準則

### 本次不做
- 不把 SnackBar 改成 TaskSession log
- 不把 dialog message 改成 LogPresenter
- 不做高階 log 搜尋 / export / filter UI
- 不改 pipeline business logic

---

## 統一規則（PR4 後要成立）

### 規則 A：凡是 session-driven log panel，一律走 `TaskSession + LogPresenter`
適用於：
- Merge
- Extractor
- Translation
- Bundler
- LM
- QC（只要它是持續追加的 log panel）

### 規則 B：SnackBar / dialog / 單次提示，不走 `TaskSession`
適用於：
- `_show_snack_bar()`
- `_show_snack()`
- dialog errors / warnings
- local one-shot user feedback

### 規則 C：不得再直接在 view 中散落 controls 膨脹防呆
例如：
- `_MAX_UI_LOG_LINES`
- `_last_log_count`
- `_last_rendered_log_count`
- 本地 truncate patch

這些都應移交給 `LogPresenter`。

---

## 候選修改檔案（第一波）

### 應納入本 PR 的高信心檔案
- `app/views/bundler_view.py`
- `app/views/lm_view.py`
- `app/views/qc_base.py`
- `app/views/qc_view.py`

### 視情況盤點，不一定納入
- 其他使用 `ListView` 但不是 log stream 的 view
- cache 查詢結果、history 面板、搜尋結果 list

> 注意：不是有 `ListView` 就等於應接 logging layer。

---

## 各 view 的接線策略

## 1. `bundler_view.py`
### 現況
- 有 `self.log_view`
- 開始執行時直接 append `"[系統] 開始執行打包..."`
- worker 回傳 line 時直接 append `ft.Text(line)`

### 設計
- 若目前沒有 `TaskSession`，需先評估是否導入最小 session-driven 架構
- 若已有背景任務線程 / subprocess 輪詢，可將 log 收斂進 `TaskSession`
- UI 端改用 `LogPresenter(mode="append")`

### 風險
- 若 bundler 現在不是 session 模式，而是 local append，需要一顆最小適配層

---

## 2. `lm_view.py`
### 現況
- 已有 `TaskSession`
- `start_ui_timer()` 內仍直接 append controls

### 設計
- 是最適合直接接 `LogPresenter(mode="append")` 的對象
- 應與 merge / extractor 對齊

### 預期收益
- 低風險高收益
- 可快速消除殘留 patch

---

## 3. `qc_base.py` / `qc_view.py`
### 現況
- 有 log panel append 行為
- 局部操作時會直接 append `ft.Text(...)`

### 設計
先確認這些 log 是否屬於：
- session-driven task log（應收）
或
- 單次 local 操作輸出（可保留 local helper）

### 判斷原則
- 若 log 會持續累積、可超過數十/數百行 → 應接 logging layer
- 若只是一次比對結果的短輸出 → 可先保留 local 邏輯

> PR4 不應為了「全部統一」而硬把不適合的場景塞進 logging layer。

---

## 實作建議：新增「UI Log Stream Adoption Checklist」

每個候選 view 在接線前都應先回答：

```markdown
- [ ] 這個頁面是否有持續追加的 log panel？
- [ ] 這些 log 是否來自背景任務 / 長任務？
- [ ] 是否已經有 TaskSession？若沒有，是否值得導入？
- [ ] 它應該用 append mode 還是 tail mode？
- [ ] 是否存在現有 local side effect（像 stats / summary / modal）需要保留在 caller？
- [ ] SnackBar / dialog 是否應繼續保持獨立？
```

---

## 驗證策略

## Validation checklist
- [ ] `python -m py_compile app/views/bundler_view.py app/views/lm_view.py app/views/qc_base.py app/views/qc_view.py`
- [ ] `uv run pytest -q tests/test_logging_*.py`
- [ ] `uv run pytest -q tests/test_bundler_view_characterization.py`
- [ ] `uv run pytest -q tests/test_lm_view_characterization.py`
- [ ] `uv run pytest -q tests/test_qc_view_characterization.py`
- [ ] 手動驗證：Bundler 長輸出不凍住、不無限膨脹
- [ ] 手動驗證：LM log panel 與 merge/extractor 行為一致
- [ ] 手動驗證：QC 若被納入，長輸出時不凍住；若未納入，需在 PR 文件中說明原因
- [ ] 手動驗證：SnackBar / dialog 行為無回歸

---

## 建議補測

### 建議新增或擴充
- `tests/test_bundler_log_presenter_integration.py`
- `tests/test_lm_log_presenter_integration.py`
- `tests/test_qc_log_presenter_integration.py`（若 QC 真納入）

### 測試重點
1. presenter 接線後 controls 不再無限膨脹
2. append mode 不重複渲染
3. tail mode（若有）只保留最後 N 筆
4. caller side effect（若有）仍正常
5. 非 session-driven snack/dialog 不受影響

---

## Cleanup 範圍

### 本次應清掉
- `bundler / lm / qc*` 內與 log panel 有關的局部 append/truncate patch
- 舊 cursor 欄位（如果存在）
- 重複的 line-color 規則（若它們本質屬於 log panel rendering）

### 本次保留
- `_show_snack_bar()`
- `_show_snack()`
- preview / modal error helpers
- 其他非 log stream UI feedback

---

## 最終交付標準

PR4 完成後，應達成：

### 已收斂
- 所有主流 **session-driven UI log stream** 都已走 `TaskSession + LogPresenter`

### 明確例外
- SnackBar / dialog / one-shot UI feedback 不屬於 logging system，保留獨立 helper

### 不再出現
- 某些長任務頁還自己手動做 `_last_log_count`
- 某些頁自己 patch truncate controls
- 未分類的 log stream 還用 local append 硬撐

---

## Rejected approaches
- 試過：把所有 UI 訊息（包含 SnackBar / dialog）都塞進新 logging layer
- 為什麼放棄：這會混淆「log stream」與「一次性 UI feedback」，讓 presenter / session 責任邊界失真，維護更困難
- 最終改採：只全面 adoption 真正屬於 log stream 的場景；SnackBar / dialog 明確列為不收斂對象

---

## Not included in this PR
- 沒有做高階 filter/search/export UI
- 沒有重構 cache query result / search result / history list 這類非 log stream 的 ListView
- 沒有修改 pipeline business logic

---

## Next step

1. 先逐檔盤點 `bundler / lm / qc_base / qc_view` 是否全部都屬於 log stream adoption 範圍
2. 優先接線最明確的 `lm_view` 與 `bundler_view`
3. 再決定 `qc_base / qc_view` 是全納入還是部分保留 local helper
4. 補 characterization + integration tests
5. 完成後正式宣告：主流 UI log stream 已全面 adoption

---

## 設計驗證出處索引

| 設計決定 | 程式碼依據 |
|---|---|
| Merge / Extractor / Translation 已收斂 | PR1–PR3 對應 view / actions 檔 |
| Bundler 仍直接 append log controls | `app/views/bundler_view.py` |
| LM 仍用 session + local append | `app/views/lm_view.py` |
| QC 仍用 local append | `app/views/qc_base.py`, `app/views/qc_view.py` |
| SnackBar 多處存在，但不屬於 log stream | 多個 `_show_snack_bar()` / `_show_snack()` 呼叫點 |

---

*本設計稿的目的是防止「主幹收斂了，但剩餘 view 逐步再分裂」；PR4 應作為全面 adoption 與分類治理的正式收尾，而不是隨手修補。*
