# Minecraft-translate 開發準則

## 程式規模：行數是防護欄，不是重構目標

行數只用來提醒「可能出現職責膨脹」。**請依職責、ownership、依賴邊界與控制流程複雜度拆分，
不要為了通過行數門檻而拆檔或抽函式。**

自動檢查在 `tests/test_view_size_limits.py`（只掃 `app/`）：

| 項目 | 正常 | 審查區（ratchet） | 硬擋 |
|---|---|---|---|
| 函式 | < 80 行 | 80–149 行 | >= 150 行 |
| View 檔案（`app/views/`） | < 800 行 | 800–1199 行 | >= 1200 行 |

- 審查區以 `tests/data/size_baseline.json` 記錄現況：**新進入審查區、或已記錄的項目變長才失敗**；
  縮短或消失不會失敗。
- 變長是合理的（例如同一職責的宣告式 Flet 版面）時，用
  `UPDATE_SIZE_BASELINE=1 pytest tests/test_view_size_limits.py` 更新快照，
  並在 PR 說明為什麼不需要拆。
- 複雜度（近似 cyclomatic）> 12、巢狀 > 4 只輸出警告，不擋 CI；新寫的程式請避免。

### 拆分原則

- 一個 module 代表一個 coherent responsibility。UI、檔案系統、worker 生命週期、
  領域邏輯、設定持久化混在同一處才是需要拆的「大檔案」。
- 單一職責的宣告式 UI 版面因宣告自然變長是可以接受的，不要硬拆成
  `view_layout.py`／`view_controls.py`／`view_helpers.py`。
- **禁止假拆分**：不要只為了把函式壓到門檻下，就抽出沒有獨立語義、沒有明確 ownership、
  只被單一函式機械呼叫的 helper（例如 `_continue_step()`、`_run_part2()`、`_process_more()`）。
  拆出來的函式要能回答「它負責什麼」（例如 `_validate_merge_inputs()`、`_collect_merge_summary()`）。

## 常用檢查

- 測試：`python -m pytest tests -q`（UI smoke 相關測試需要 playwright，沒安裝時略過）
- Lint／格式：`ruff check <檔案>`、`ruff format <檔案>`（只格式化自己動到的檔案）
- 產生文件：新增 `except Exception` 後執行 `python tools/gen_exception_inventory.py`；
  修改設定 schema 後執行 `python tools/gen_config_example.py`
- 後台記錄與畫面訊息成對出現時文字必須一致（見 `tests/test_log_pair_contract.py`）
