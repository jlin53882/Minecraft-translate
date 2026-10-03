# 測試策略說明

本文件規範測試分層、回歸流程、執行方式，以及行為風險盤點的使用原則。

## 測試組織
- **總數量**：以 `uv run --isolated python -m pytest --collect-only -q` 為準（不在文件內硬寫數字，避免過期）
- **測試分類**：
  - **單元測試**（unit）：`tests/test_*.py`
  - **表徵測試**（characterization test）：`tests/test_*_characterization.py`
- **`conftest.py` 提供的 helper functions**（**非 pytest fixture**，為一般 Python 函式，需從 `conftest` import 後直接呼叫）：
  - `temp_dir()` — 提供臨時目錄，測試結束後自動清理（內部使用 `tempfile.mkdtemp` + `shutil.rmtree`）
  - `mock_config()` — 回傳測試用 mock 設定字典（`test_mode: True`）
  - `mock_empty_config()` — 回傳空設定字典，用於測試預設值行為
  - `mock_page()`、`mock_filepicker()` — 同為一般 helper 函式，回傳 Flet 測試替身
  - `slow` marker — pytest marker，可用 `-m "not slow"` 排除慢速測試
- **真正的 pytest fixtures**（`autouse`，自動套用到每個測試）：`_isolate_test_runtime_writes`（把執行期寫入導向 `tmp_path`）、`_patch_flet_page_property`

## 各測試類型說明

以下依測試目的區分單元測試與表徵測試。

### 單元測試（unit）
位置：`tests/test_*.py`
目標：驗證每個函式邏輯正確性。涵蓋翻譯、快取、設定、UI 元件等各模組。

### 表徵測試（characterization test）
位置：`tests/test_*_characterization.py`
- `test_bundler_view_characterization.py`
- `test_config_view_characterization.py`
- `test_extractor_view_characterization.py`
- `test_icon_preview_view_characterization.py`
- `test_lm_view_characterization.py`
- `test_lookup_view_characterization.py`
- `test_merge_view_characterization.py`
- `test_qc_view_characterization.py`
- `test_rules_view_characterization.py`
- `test_translation_view_characterization.py`

目標：記錄現有 UI 行為，防止意外 regression。

### Integration / pipeline test

當風險跨越多個模組、檔案格式或輸出階段時，使用固定暫存 fixture 驗證完整資料流，
例如 export → clean → translate fixture → inject。這類測試應斷言最終輸出內容、保留欄位、
來源檔案是否未被修改，以及錯誤／計數狀態；只斷言函式被呼叫或計數器增加，不足以證明
pipeline 行為正確。

### Fake fixture 與真實服務的界線

Fake translator、fake API、固定 clock 與 `tmp_path` fixture 適合建立 deterministic
regression，能證明本地解析、轉換、輸出與錯誤處理的行為。它們不能證明供應商 API、網路、
認證、模型品質或大型真實模組包的相容性。真實網路／真實 API 不應成為一般回歸測試的必要
條件；需要時應另列為非 deterministic 的整合或人工驗收。

## 如何執行測試

```bash
uv run --isolated python -m pytest                    # 全部
uv run --isolated python -m pytest tests/test_xxx.py  # 單一檔案
uv run --isolated python -m pytest -k "cache"        # 只跑含 "cache" 的測試
uv run --isolated python -m pytest -m "not slow"     # 排除 slow marker 的測試
uv run --isolated python -m pytest -x                 # 遇錯即停
```

## Bug regression workflow

遇到 production bug 時，依下列順序建立可維護的證據：

```text
Production bug
→ failing regression
→ search the same bug class and adjacent callers
→ focused GREEN test
→ impacted subsystem / pipeline tests
→ full regression
→ real-Flet acceptance when UI or runtime behavior is affected
```

測試新增或修正後，先確認失敗測試確實重現問題，再確認 GREEN 測試斷言輸出、狀態、事件或
檔案內容。搜尋同一 bug class 的目的，是避免只修一個字串或單一路徑而漏掉相鄰格式、狀態
與 caller；測試數量本身不代表行為覆蓋完整。

## 覆蓋率

目前 `pyproject.toml` 與 `uv.lock` **沒有安裝 pytest-cov**，因此不得把
`pytest --cov` 當成既定命令。若日後核准 coverage 工具，先加入 dev
dependency、更新 lock，再以「風險 × 覆蓋」使用，不設全專案百分比門檻。
引擎風險路徑、現有行為證據與尚缺邊界見 `docs/TEST_RISK_COVERAGE.md`；
該 inventory 不以測試數量或 import 測試推論行為覆蓋率。

## 真實 UI 與效能驗收

- 真實 Flet 截圖／跨頁任務矩陣：`docs/UI_VISUAL_ACCEPTANCE.md`
- 可重現效能契約：`docs/PERFORMANCE.md`

## 變更與測試層級

- 純函式、格式解析、錯誤分類：unit／characterization。
- 跨檔案格式、輸出寫回、checkpoint、cache 或翻譯流程：integration／pipeline fixture。
- Flet layout、Dialog、導航、任務生命週期或 renderer 行為：real-Flet／Playwright acceptance，
  並依 `docs/UI_VISUAL_ACCEPTANCE.md` 的契約人工檢查截圖。
- 高風險引擎路徑、取消／恢復、資料遺失或覆寫風險：先跑 focused regression，再跑受影響
  subsystem，最後視範圍執行 full regression。

## 新增測試的 SOP

1. 在 `tests/test_<module>.py` 新增（若無則新建）
2. 視需要使用 pytest 內建 fixtures（如 `tmp_path`、`monkeypatch`），或從 `conftest.py` import 共用 helper 函式（`temp_dir`、`mock_config` 等）
3. 測試函式命名：`test_<function>_<scenario>`
4. 提交前確認 `uv run pytest` 全部通過

## 注意事項

- **不要 mock 太多層次**：unit test 應隔離，專注單一函式邏輯
- **characterization test 用 `-x`**：遇錯即停，防止意外通過
- **測試失敗時**：第一步先確認是否為 CI 環境差異（如 `charset_normalizer` 的虛擬環境問題）
