# PR34 Phase 0 盤點報告（停在 Phase 0，未進入 Phase 1）

## 進度狀態
- PR33 已完成並推送：`1161975`
- 目前已進入 PR34，停在 Phase 0（尚未改 PR34 目標程式碼）

---

## Phase 0 結論
PR34 原本是「non-UI guard tests 擴充」，但經過 PR31~PR33 後，前半段安全網其實已經補了不少：
- PR31：shared JSON/path helpers 測試已補
- PR32：shared lang text detection 測試已補
- PR33：pipeline logging bootstrap 時機測試已補

所以 **PR34 現在最值得補的 guard tests，應該集中在 PR35~PR37 的後續風險點**：
1. `translation_tool/core/lm_translator_main.py`
2. `translation_tool/core/lang_merger.py`
3. `translation_tool/utils/cache_manager.py`（補 façade 邊界，不是從 0 開始）

---

## 現況盤點

### A) `lm_translator_main.py` 測試覆蓋
檢索：
- `rg -n "lm_translator_main|translate_batch_smart|find_lang_json|extract_translatables|safe_json_loads" tests --glob "*.py"`

結果：
- **無命中**

結論：
- 目前沒有直接保護 `lm_translator_main.py` 的 guard tests。
- 這是 PR35 前最大的測試缺口。

### B) `lang_merger.py` 測試覆蓋
檢索：
- `rg -n "from translation_tool.core.lang_merger|merge_zhcn_to_zhtw_from_zip|export_filtered_pending|parse_lang_text|dump_lang_text" tests --glob "*.py"`

結果：
- 僅在 `test_pipeline_logging_bootstrap.py` 看到 `merge_service` 被 monkeypatch `merge_zhcn_to_zhtw_from_zip`
- **沒有直接測 `lang_merger.py` 行為**

結論：
- 目前沒有真正保護 `lang_merger.py` 內部邏輯的 guard tests。
- 這是 PR36 前的核心缺口。

### C) `cache_manager.py` 測試覆蓋
檢索：
- `rg -n "cache_manager|reload_translation_cache|save_translation_cache|search_cache|get_cache_entry|get_cache_dict_ref" tests --glob "*.py"`

結果：
- 已有覆蓋：
  - `test_path_resolution.py`
  - `test_cache_search_orchestration.py`
  - `test_cache_store.py`
- 另外也有：`test_cache_shards.py`

結論：
- `cache_manager` **不是零覆蓋**，而是已有部分安全網。
- PR37 比較像是要補「façade 對外 API 邊界」測試，而不是整包重寫測試。

---

## 建議 PR34 Phase 1 具體測試清單

### 1) 先補 `lm_translator_main.py` guard tests
建議新增 `tests/test_lm_translator_main_guards.py`：
- `safe_json_loads`：
  - 純 JSON object 可 parse
  - 帶 ```json code fence 可 parse
  - 無有效 JSON 時會拋錯
- `find_lang_json`：
  - 可找到 `assets/.../lang/*.json`
- `extract_translatables`：
  - lang 檔只抽字串值
  - 非字串值略過
- `set_by_path`：
  - 巢狀 path 可正確回填

### 2) 補 `lang_merger.py` guard tests
建議新增 `tests/test_lang_merger_guards.py`：
- `parse_lang_text` / `dump_lang_text` round-trip
- `collapse_lang_lines` 基本樣本
- `is_mc_standard_lang_path` 判定樣本
- `export_filtered_pending`：固定輸入可產生預期輸出

> 註：`merge_zhcn_to_zhtw_from_zip` 本體若要測，建議用最小 zip fixture；若 Phase 1 時間不夠，可先不碰 generator 本體，先補 helper guards。

### 3) 補 `cache_manager` façade 邊界測試
建議新增 `tests/test_cache_manager_api_surface.py`：
- 主要 public API 可 import
- `reload_translation_cache / save_translation_cache / search_cache / get_cache_entry / get_cache_dict_ref` 仍存在
- 若 PR37 要加 `__all__`，這顆測試可先準備對照表

---

## baseline 測試
命令：
- `uv run pytest -q --basetemp=.pytest-tmp\pr34-phase0 -o cache_dir=.pytest-cache\pr34-phase0`

結果：
- `64 passed in 1.05s`

---

## Phase 0 建議
- ✅ 可以進 Phase 1
- 但 PR34 不要再寫成抽象方向，應該直接落成 2~3 個具名測試檔與明確案例
- 我建議優先順序：
  1. `test_lm_translator_main_guards.py`
  2. `test_lang_merger_guards.py`
  3. `test_cache_manager_api_surface.py`

---

## 目前停點
- ✅ PR34 Phase 0 完成
- ⛔ 尚未進入 Phase 1（等待你確認放行）
