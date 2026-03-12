# PR42 設計稿：`translation_tool/core/lang_merge_content.py` 內容處理層拆分

## Summary
PR42 要把 `lang_merge_content.py` 從『內容 patch / copy / cleanup 全混在一起』的狀態，拆成幾個責任單純的模組。重點是把 content handling 從 `lang_merger` 主流程旁邊切乾淨，而不是重寫 merge 行為。

---

## Phase 0 盤點
- 目前 `translation_tool/core/lang_merge_content.py` 約 636 行。
- 主要 public / semi-public 能力集中在 `_patch_localized_content_json()`、`_process_content_or_copy_file()`、`remove_empty_dirs()`、`export_filtered_pending()`。
- repo 目前有 `tests/test_lang_merger_guards.py`、`tests/test_lang_merger_zip_baseline.py`，但缺直接鎖 content patch / pending export 細節的 focused tests。
- 這層與 zip merge 主流程高度相關，若不先切乾淨，之後很難分辨 bug 是 merge pipeline 還是 content policy。

---

## 設計範圍
- 新增 `translation_tool/core/lang_merge_content_patchers.py`，集中 localized content patching 與 patchouli content rewrite。
- 新增 `translation_tool/core/lang_merge_content_copy.py`，集中 content copy / quarantine policy 與 copy-or-patch 分流。
- 新增 `translation_tool/core/lang_merge_pending.py`，集中 `remove_empty_dirs()`、`export_filtered_pending()` 與 pending export cleanup。
- `lang_merge_content.py` 本身退成 façade，對 `lang_merger` 仍維持原 import 契約。
- focused tests 拆成 `tests/test_lang_merge_content_patchers.py`、`tests/test_lang_merge_pending_export.py`，並沿用既有 lang merger baseline tests 驗證主流程沒變。

---

## Validation checklist
- [ ] `rg -n "_patch_localized_content_json|_process_content_or_copy_file|remove_empty_dirs|export_filtered_pending" translation_tool/core/lang_merge_content*.py`
- [ ] `uv run pytest -q tests/test_lang_merge_content_patchers.py tests/test_lang_merge_pending_export.py tests/test_lang_merger_guards.py tests/test_lang_merger_zip_baseline.py --basetemp=.pytest-tmp\pr42 -o cache_dir=.pytest-cache\pr42`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr42-full -o cache_dir=.pytest-cache\pr42-full`

---

## Rejected approaches
1) 試過：直接把 content 邏輯塞回 `lang_merger.py` 旁邊，再靠區塊註解把責任分開。
2) 為什麼放棄：這種做法看起來省檔案，但其實只是讓 fatigue 往更大的檔案回流，後面根本沒法做 focused testing。
3) 最終改採：拆成 patch / copy / pending 三層，讓每層能被單獨測。

---

## Not included in this PR
- 不改 zip I/O codec，不改 `.lang` 解析策略。
- 不處理 `lang_merger.py` 其他歷史相容層。
- 不新增新格式支援。

---

## Next step
- PR43 進入 FTB pipeline，把另一條 non-UI 大流程拆成可測責任區塊。
