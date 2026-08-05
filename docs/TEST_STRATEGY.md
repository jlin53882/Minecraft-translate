# 測試策略說明

## 測試組織
- **總數量**：1905 個測試（collect-only 收集結果）
- **測試分類**：
  - **單元測試**（unit）：`tests/test_*.py`
  - **表徵測試**（characterization test）：`tests/test_*_characterization.py`
- **`conftest.py` 提供的 helper functions**（**非 pytest fixture**，為一般 Python 函式，測試時直接當引數傳入）：
  - `temp_dir()` — 提供臨時目錄，測試結束後自動清理（內部使用 `tempfile.mkdtemp` + `shutil.rmtree`）
  - `mock_config()` — 回傳測試用 mock 設定字典（`test_mode: True`）
  - `mock_empty_config()` — 回傳空設定字典，用於測試預設值行為
  - `slow` marker — pytest marker，可用 `-m "not slow"` 排除慢速測試

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
uv run pytest                    # 全部（1905 個）
uv run pytest tests/test_xxx.py  # 單一檔案
uv run pytest -k "cache"        # 只跑含 "cache" 的測試
uv run pytest -m "not slow"     # 排除 slow marker 的測試
uv run pytest -x                # 遇錯即停（適合 characterization test）
```

## 覆蓋率

```bash
uv run pytest --cov=translation_tool --cov=app
```

## 新增測試的 SOP

1. 在 `tests/test_<module>.py` 新增（若無則新建）
2. 使用 `conftest.py` 的 fixtures（`temp_dir`、`mock_config` 等）
3. 測試函式命名：`test_<function>_<scenario>`
4. 提交前確認 `uv run pytest` 全部通過

## 注意事項

- **不要 mock 太多層次**：unit test 應隔離，專注單一函式邏輯
- **characterization test 用 `-x`**：遇錯即停，防止意外通過
- **測試失敗時**：第一步先確認是否為 CI 環境差異（如 `charset_normalizer` 的虛擬環境問題）
