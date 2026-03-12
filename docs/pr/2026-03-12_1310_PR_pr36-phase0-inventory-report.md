# PR36 Phase 0 盤點報告（停在 Phase 0，未進入 Phase 1）

## 進度狀態
- PR35 已完成並推送：`97e1441`
- 目前已進入 PR36，停在 Phase 0（尚未改 PR36 目標程式碼）

---

## Phase 0 結論（先講重點）
`translation_tool/core/lang_merger.py` 目前 1256 行、18 個頂層函式，責任混雜，確實該拆。  
但依 PR36 設計稿的硬規則：**沒有 baseline 樣本，不進 Phase 1。**

我已確認：
- caller 依賴很單純，主要是 `merge_service.py`
- helper guards 已在 PR34 補了一部分
- **目前 repo 內沒有可直接拿來當 PR36 baseline 的專用 zip fixture**

=> 所以這顆現在的正確停點是：
**先補 1 組最小可重現 zip 樣本 + baseline 對照規格，再進 Phase 1。**

---

## 1) caller 依賴盤點
目前直接依賴 `lang_merger.py` 的位置：
- `app/services_impl/pipelines/merge_service.py`
  - `from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip`
- `tests/test_pipeline_logging_bootstrap.py`
  - 只 monkeypatch `merge_service.merge_zhcn_to_zhtw_from_zip`
- `tests/test_lang_merger_guards.py`
  - 直接測 helper：
    - `parse_lang_text`
    - `dump_lang_text`
    - `is_mc_standard_lang_path`
    - `export_filtered_pending`

結論：
- **真正的 runtime caller 幾乎只有 `merge_service.py`**
- 這對 Phase 1 很有利，因為 caller 面小

---

## 2) `lang_merger.py` 搬移清單（逐符號）

### A. `lang_merge_zip_io.py`
- `_read_text_from_zip`
- `_read_json_from_zip`
- `_write_bytes_atomic`
- `_write_text_atomic`
- `quarantine_copy_from_zip`

### B. `lang_codec.py`
- `try_repair_lang_line`
- `collapse_lang_lines`
- `parse_lang_text`
- `dump_lang_text`
- `is_mc_standard_lang_path`
- `pick_first_not_none`
- `normalize_patchouli_book_root`

### C. `lang_merge_content.py`
- `_patch_localized_content_json`
- `_process_content_or_copy_file`
- `remove_empty_dirs`
- `export_filtered_pending`

### D. `lang_merge_pipeline.py`
- `_process_single_mod`

### E. `lang_merger.py` 保留
- `merge_zhcn_to_zhtw_from_zip`（主入口）
- 初期可保留薄轉接 / re-export

---

## 3) 目前已有的安全網
PR34 已補：
- `collapse_lang_lines`
- `parse_lang_text`
- `dump_lang_text`
- `is_mc_standard_lang_path`
- `export_filtered_pending`

baseline：
- `uv run pytest -q --basetemp=.pytest-tmp\pr36-phase0 -o cache_dir=.pytest-cache\pr36-phase0`
- 結果：`83 passed in 1.10s`

結論：
- helper 層已有基本 guard
- **缺的是 generator 主流程的固定樣本比對**

---

## 4) baseline 樣本現況
我檢查 repo 內現有 `.zip`：
- 只找到 `.agentlens/minecraft_translator_flet_analysis_bundle_v4.zip`

這個不是 PR36 可用的 merge baseline，原因：
- 它是分析包，不是 lang merge 輸入樣本
- 內容與 `merge_zhcn_to_zhtw_from_zip` 的預期輸入契約不對齊
- 不適合拿來當回歸基準

=> 目前判定：**沒有合格 baseline zip 樣本**

---

## 5) Phase 1 前必補的 baseline 規格
至少要準備 1 組最小 zip fixture，且能記錄以下 baseline：
1. 輸出檔案數
2. 關鍵 lang 檔 key 數
3. pending / filtered_pending 結果
4. 是否產生 quarantine / skipped 檔
5. log / error 摘要（至少確認無異常中斷）

建議 fixture 內容最小化：
- `assets/<mod>/lang/en_us.json`
- `assets/<mod>/lang/zh_cn.json`
- （可選）`assets/<mod>/lang/zh_tw.json`
- 1 個非 lang JSON（驗證 content copy / patch 行為）

---

## Phase 0 建議
- ✅ caller 與 helper 盤點已完成
- ⛔ **不要直接進 PR36 Phase 1**
- 下一步正解應該是二選一：
  1. 先在 PR36 補一組最小 zip fixture + baseline 測試，再開始切模組
  2. 或另開一顆前置 PR（較不建議，顆粒會太碎）

我建議走 **1**：PR36 先做 baseline fixture，再做 Phase 1 切模組。

---

## 目前停點
- ✅ PR36 Phase 0 完成
- ⛔ 尚未進入 Phase 1（缺 baseline 樣本，依設計稿規則暫停）
