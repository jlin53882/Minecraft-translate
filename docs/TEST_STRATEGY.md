# 測試策略說明

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

## 如何執行測試

```bash
uv run --isolated python -m pytest                    # 全部
uv run --isolated python -m pytest tests/test_xxx.py  # 單一檔案
uv run --isolated python -m pytest -k "cache"        # 只跑含 "cache" 的測試
uv run --isolated python -m pytest -m "not slow"     # 排除 slow marker 的測試
uv run --isolated python -m pytest -x                 # 遇錯即停
```

## 覆蓋率

目前 `pyproject.toml` 與 `uv.lock` **沒有安裝 pytest-cov**，因此不得把
`pytest --cov` 當成既定命令。若日後核准 coverage 工具，先加入 dev
dependency、更新 lock，再以「風險 × 覆蓋」使用，不設全專案百分比門檻。

## 真實 UI 與效能驗收

- 真實 Flet 截圖／跨頁任務矩陣：`docs/UI_VISUAL_ACCEPTANCE.md`
- 可重現效能契約：`docs/PERFORMANCE.md`

## 新增測試的 SOP

1. 在 `tests/test_<module>.py` 新增（若無則新建）
2. 視需要使用 pytest 內建 fixtures（如 `tmp_path`、`monkeypatch`），或從 `conftest.py` import 共用 helper 函式（`temp_dir`、`mock_config` 等）
3. 測試函式命名：`test_<function>_<scenario>`
4. 提交前確認 `uv run pytest` 全部通過

## 注意事項

- **不要 mock 太多層次**：unit test 應隔離，專注單一函式邏輯
- **characterization test 用 `-x`**：遇錯即停，防止意外通過
- **測試失敗時**：第一步先確認是否為 CI 環境差異（如 `charset_normalizer` 的虛擬環境問題）
