# PR42：lang merge content split

## Summary
這顆 PR 把 `translation_tool/core/lang_merge_content.py` 內混在一起的 localized patch / content copy / pending export 拆成三個子模組，讓 `lang_merger` 依賴的 content layer 變得可讀、可測，但維持原 import 契約不變。目標是切責任，不是改 merge 行為。

---

## Phase 1 完成清單
- [x] 做了：新增 `lang_merge_content_patchers.py`，收納 `_patch_localized_content_json()` 的核心實作。
- [x] 做了：新增 `lang_merge_content_copy.py`，收納 `_process_content_or_copy_file()` 的主流程與 patchouli/content copy 分流。
- [x] 做了：新增 `lang_merge_pending.py`，收納 `remove_empty_dirs()` 與 `export_filtered_pending()` 的實作。
- [x] 做了：把 `lang_merge_content.py` 改成 façade wrapper，保留既有 import surface 與 monkeypatch seam。
- [x] 做了：補 `tests/test_lang_merge_content_patchers.py`、`tests/test_lang_merge_pending_export.py`。
- [ ] 未做：`lang_merger.py` 其他歷史相容層整理（原因：本 PR 僅處理 content layer 拆分）。

---

## What was done

### 1. 把 localized JSON patching 拆到 patchers 模組
新增 `translation_tool/core/lang_merge_content_patchers.py`，集中處理：
- 讀取 `zh_cn` localized JSON
- 進行 S2TW 轉換
- pretty-print 後比對與寫入
- JSON parse 失敗時 quarantine

### 2. 把 content copy / patchouli 分流拆到 copy 模組
新增 `translation_tool/core/lang_merge_content_copy.py`，集中處理：
- patchouli 書籍 root 判定與路徑重寫
- content copy / patch / quarantine policy
- 純文字檔與 JSON 檔的 copy-or-transform 分流

### 3. 把 pending export / cleanup 拆到獨立模組
新增 `translation_tool/core/lang_merge_pending.py`：
- `remove_empty_dirs_impl()`
- `export_filtered_pending_impl()`

這樣 pending cleanup 與 content patch/copy 不再擠在同一個大檔案裡。

### 4. 保留 façade wrapper，守住既有 import 與 monkeypatch 契約
`translation_tool/core/lang_merge_content.py` 現在改成薄 façade：
- 對外仍提供 `_patch_localized_content_json()`、`_process_content_or_copy_file()`、`remove_empty_dirs()`、`export_filtered_pending()`
- wrapper 會把 façade module 目前的依賴（例如 `recursive_translate_dict`、`load_config`、`quarantine_copy_from_zip`）傳給子模組實作
- 這樣既有 caller 與測試的 monkeypatch surface 不會因為模組拆分直接斷掉

---

## Important findings
- `tests/test_lang_merger_zip_baseline.py` 會 monkeypatch `lang_merge_content.load_config` / `recursive_translate_dict` / `apply_replace_rules`，所以 `lang_merge_content.py` 不能只是單純 re-export；一定要保留 wrapper，才能守住既有測試 seam。
- 新補的 patcher focused test 一開始寫錯預期：`_patch_localized_content_json()` 本來就會做 S2TW，`简体内容` 轉成 `簡體內容` 才是正確行為；修正後測試與主流程 baseline 一致。
- `lang_merge_content.py` 實際長度約 35 KB，不只是「稍微大」，是真的已經到了該拆的程度。

---

## Validation checklist
- [x] `rg -n "_patch_localized_content_json|_process_content_or_copy_file|remove_empty_dirs|export_filtered_pending" translation_tool/core/lang_merge_content.py translation_tool/core/lang_merge_content_patchers.py translation_tool/core/lang_merge_content_copy.py translation_tool/core/lang_merge_pending.py`
- [x] `uv run pytest -q tests/test_lang_merge_content_patchers.py tests/test_lang_merge_pending_export.py tests/test_lang_merger_guards.py tests/test_lang_merger_zip_baseline.py --basetemp=.pytest-tmp\pr42 -o cache_dir=.pytest-cache\pr42`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr42-full -o cache_dir=.pytest-cache\pr42-full`

## Test result
```text
$ rg -n "_patch_localized_content_json|_process_content_or_copy_file|remove_empty_dirs|export_filtered_pending" translation_tool/core/lang_merge_content.py translation_tool/core/lang_merge_content_patchers.py translation_tool/core/lang_merge_content_copy.py translation_tool/core/lang_merge_pending.py
translation_tool/core/lang_merge_content.py:20:from .lang_merge_pending import export_filtered_pending_impl, remove_empty_dirs_impl
translation_tool/core/lang_merge_content.py:31:def _patch_localized_content_json(
translation_tool/core/lang_merge_content.py:53:def _process_content_or_copy_file(
translation_tool/core/lang_merge_content.py:76:        patch_localized_content_json_fn=_patch_localized_content_json,
translation_tool/core/lang_merge_content.py:82:def remove_empty_dirs(root_dir: str):
translation_tool/core/lang_merge_content.py:83:    return remove_empty_dirs_impl(root_dir, logger_override=logger)
translation_tool/core/lang_merge_content.py:86:def export_filtered_pending(pending_root: str, output_root: str, min_count: int):
translation_tool/core/lang_merge_content.py:87:    return export_filtered_pending_impl(
translation_tool/core/lang_merge_content.py:96:    "_patch_localized_content_json",
translation_tool/core/lang_merge_content.py:97:    "_process_content_or_copy_file",
translation_tool/core/lang_merge_content.py:98:    "remove_empty_dirs",
translation_tool/core/lang_merge_content.py:99:    "export_filtered_pending",
translation_tool/core/lang_merge_pending.py:10:def remove_empty_dirs_impl(root_dir: str, *, logger_override=None) -> None:
translation_tool/core/lang_merge_pending.py:25:def export_filtered_pending_impl(

$ uv run pytest -q tests/test_lang_merge_content_patchers.py tests/test_lang_merge_pending_export.py tests/test_lang_merger_guards.py tests/test_lang_merger_zip_baseline.py --basetemp=.pytest-tmp\pr42 -o cache_dir=.pytest-cache\pr42
.............                                                            [100%]
13 passed in 0.19s

$ uv run pytest -q --basetemp=.pytest-tmp\pr42-full -o cache_dir=.pytest-cache\pr42-full
........................................................................ [ 75%]
........................                                                 [100%]
96 passed in 1.28s
```

---

## Rejected approaches
1) 試過：把拆分後的函式直接從子模組 re-export，讓 `lang_merge_content.py` 完全不留 wrapper。
   - 為什麼放棄：既有 baseline tests 會 monkeypatch `lang_merge_content` 上的依賴；若只做 re-export，patch 不會傳進子模組實作，測試 seam 會直接斷掉。
   - 最終改採：保留 façade wrapper，把目前 module 依賴顯式傳給子模組實作。

2) 試過：新 patcher focused test 直接鎖原文 `简体内容` 不變。
   - 為什麼放棄：這跟 `_patch_localized_content_json()` 的真實契約衝突；實作本來就會做 S2TW，測試等於自己寫錯。
   - 最終改採：改鎖真實輸出 `簡體內容`，讓 focused test 與 baseline 一致。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 zip I/O codec
- 沒有改 `.lang` 解析策略
- 沒有處理 `lang_merger.py` 其他歷史相容層
- 沒有新增新格式支援

---

## Next step

### PR43
- 進入 FTB pipeline，把另一條 non-UI 大流程拆成可測責任區塊。
- 若後續要再瘦身 `lang_merge_content.py` façade，可等所有 caller/測試 seam 都穩定後再做 cleanup PR。
