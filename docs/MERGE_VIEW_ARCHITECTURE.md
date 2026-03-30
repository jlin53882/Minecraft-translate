# MERGE_VIEW_ARCHITECTURE.md

## 翻譯流程定位

```
Translate（LM翻譯） → Merge（MergeView） → 寫回 JAR
```

MergeView 位於翻譯流程的**第二步**，負責將翻譯後的 ZIP 內容（zh_cn）合併為繁體中文（zh_tw），並依類型分類輸出。

---

## 主要 UI 結構

四個 `styled_card` 垂直排列：

| 卡片 | 內容 |
|------|------|
| ZIP 清單 | `pick_zip_button` 新增 ZIP、`zip_list_view` 顯示已選 ZIP |
| 輸出與選項 | 輸出資料夾路徑、三個設定Section（一般、zh_cn處理、Patchouli進階） |
| 執行狀態 | `status_chip` 晶片、`progress_bar` 進度條、`start_button` 開始按鈕 |
| 執行日誌 | `log_view` ListView，深色背景，LogPresenter 接管渲染 |

### 核心設定開關
- `only_lang_checkbox`：只處理 lang 檔，跳過其他內容
- `process_zh_cn_switch`：處理/略過 zh_cn 檔（關閉時連動停用另外兩個開關）
- `skip_zh_cn_switch`：lang 模式時跳過 zh_cn
- `patchouli_skip_zh_cn_switch`：zh_cn 達門檻時跳過對應 en_us
- `patchouli_threshold_field`：en_us 跳過門檻（預設 0.5）

---

## Merge 呼叫鏈

```
[UI] start_merge()
  → _run_merge() [thread]
      → run_merge_zip_batch_service()   [merge_service.py]
          → merge_zhcn_to_zhtw_from_zip()  [lang_merger.py]
              → ZIP 掃描 → 分類 mod → 各模組處理步驟
```

`run_merge_zip_batch_service` 本身是 **generator**，由 UI Poller（`poll()` 執行緒）每 0.1 秒輪詢 `session.snapshot()`，同步：
- `progress_bar.value` → 進度
- `LogPresenter.sync()` → 日誌渲染（append 模式，max 2000 行）
- `status_chip` → 狀態文字與顏色

完成時顯示 `_show_merge_summary()` 對話框，內含：
- 成功/失敗 ZIP 數量
- 各輸出類型檔案數（lang_output / 待翻譯 / patchouli_output / other_output / errordata_output）
- 失敗 ZIP 詳細錯誤

---

## MergeView 與 Session 的關係

- `TaskSession(max_logs=2000)`：儲存任務日誌與進度狀態
- `LogPresenter(mode="append", max_ui_lines=2000)`：接管 ListView 渲染，防止 UI controls 數量膨脹凍住
- Poller 執行緒在 `status == "DONE" | "ERROR"` 時主動停止（`self._ui_stop.set()`）
