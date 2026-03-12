# PR36 Phase 1 執行 + Validation 回報（停在 commit 前）

> 狀態：PR36 已完成實際拆分，Validation 已跑完，**尚未 commit/push**。

## 本次實作內容（non-UI）

### 新增檔案
- `translation_tool/core/lang_merge_zip_io.py`
- `translation_tool/core/lang_codec.py`
- `translation_tool/core/lang_merge_content.py`
- `translation_tool/core/lang_merge_pipeline.py`

### 修改檔案
- `translation_tool/core/lang_merger.py`
- `tests/test_lang_merger_zip_baseline.py`

---

## 拆分結果
### `lang_merge_zip_io.py`
- `_read_text_from_zip`
- `_read_json_from_zip`
- `_write_bytes_atomic`
- `_write_text_atomic`
- `quarantine_copy_from_zip`

### `lang_codec.py`
- `try_repair_lang_line`
- `collapse_lang_lines`
- `parse_lang_text`
- `dump_lang_text`
- `is_mc_standard_lang_path`
- `pick_first_not_none`
- `normalize_patchouli_book_root`

### `lang_merge_content.py`
- `_patch_localized_content_json`
- `_process_content_or_copy_file`
- `remove_empty_dirs`
- `export_filtered_pending`

### `lang_merge_pipeline.py`
- `_process_single_mod`

### `lang_merger.py` 保留
- `merge_zhcn_to_zhtw_from_zip`
- 對 helper 的 re-export（透過 import 回 module namespace）

---

## 相容策略
- `merge_service.py` 無需改動，仍可：
  - `from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip`
- helper guard tests 仍可從 `translation_tool.core.lang_merger` 取 `parse_lang_text / dump_lang_text / export_filtered_pending` 等舊符號
- baseline fixture 測試已更新 monkeypatch 位置，現在會 patch 到拆分後真正使用的新模組

---

## Validation checklist 實際輸出

### 1) import smoke
```text
> uv run python -c "from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip; print('lang-merger-import-ok')"
lang-merger-import-ok

> uv run python -c "from app.services_impl.pipelines.merge_service import run_merge_zip_batch_service; print('merge-service-import-ok')"
merge-service-import-ok
```

### 2) baseline fixture（PR36 關鍵）
```text
> uv run pytest -q tests/test_lang_merger_zip_baseline.py --basetemp=.pytest-tmp\pr36-phase1-baseline -o cache_dir=.pytest-cache\pr36-phase1-baseline
.                                                                        [100%]
1 passed in 0.12s
```

### 3) 全量測試
```text
> uv run pytest -q --basetemp=.pytest-tmp\pr36-phase1 -o cache_dir=.pytest-cache\pr36-phase1
........................................................................ [ 85%]
............                                                             [100%]
84 passed in 1.15s
```

---

## 數字對照
- baseline fixture 建立後：`84 passed`
- PR36 Phase 1 拆分後：`84 passed`
- 差異：`+0`

=> 這代表在既有安全網下，這次重構屬於「內部佈線變更，對外契約未破壞」。

---

## 補充說明
- Phase 1 期間先踩到 2 個純搬移層級問題，已修正：
  1. `dump_json_bytes/get_text_processor` import 路徑應來自 `lang_processing_format`
  2. baseline 測試的 monkeypatch 位置需跟著新模組邊界更新
- 修正後 baseline fixture 與全量 pytest 都已回綠

---

## 目前停點
- ✅ PR36 Phase 1 與 Validation 完成
- ⛔ 尚未 commit/push（等你確認放行）
