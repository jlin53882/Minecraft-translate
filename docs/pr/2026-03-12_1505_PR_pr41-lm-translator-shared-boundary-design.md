# PR41 設計稿：`translation_tool/core/lm_translator_shared.py` 邊界整理

## Summary
PR41 要處理的不是『把 helper 再拆碎』，而是把目前 `lm_translator_shared.py` 裡不同抽象層的內容拆開，讓 preview、cache split、recording、translate loop 不再全部擠在同一個 shared 模組。這顆 PR 要讓 PR40 抽出的 orchestration 有乾淨下層可接。

---

## Phase 0 盤點
- 目前 `translation_tool/core/lm_translator_shared.py` 約 418 行，內含 `CacheRule`、cache split、preview writer、`TranslationRecorder`、translate loop。
- `lm_translator.py` 與未來 pipeline 入口都會依賴這層；若 shared 邊界不清，PR40 只是在表面把複雜度平移。
- 目前 repo 沒有專門鎖 preview writer / recorder 的 focused tests，屬於高風險 shared seam。
- shared 層現在既有 domain rule，也有 I/O writer，還有迴圈控制，責任過寬。

---

## 設計範圍
- 新增 `translation_tool/core/lm_translator_shared_cache.py`，保留 `CacheRule`、default cache rule 與 fast split helper。
- 新增 `translation_tool/core/lm_translator_shared_preview.py`，集中 `write_dry_run_preview()`、`write_cache_hit_preview()` 與 preview 相關 touch bookkeeping。
- 新增 `translation_tool/core/lm_translator_shared_recording.py`，集中 `TranslationRecorder` 與 translated item 落盤前的 record/update 邏輯。
- 新增 `translation_tool/core/lm_translator_shared_loop.py`，只保留 batch loop、預設 batch size、loop result 型別。
- 暫時保留 `lm_translator_shared.py` 作薄 façade / import aggregator，避免 PR41 把 caller migration 風險一起吞掉；真正 cleanup 留給 PR57。
- 補 `tests/test_lm_translator_shared_preview.py`、`tests/test_lm_translator_shared_recording.py`，外加 loop smoke。

---

## Validation checklist
- [ ] `rg -n "class CacheRule|class TranslationRecorder|def translate_items_with_cache_loop|def write_dry_run_preview|def write_cache_hit_preview" translation_tool/core/lm_translator_shared*.py`
- [ ] `uv run pytest -q tests/test_lm_translator_shared_preview.py tests/test_lm_translator_shared_recording.py --basetemp=.pytest-tmp\pr41 -o cache_dir=.pytest-cache\pr41`
- [ ] `uv run pytest -q tests/test_lm_translator_cache_split.py tests/test_lm_translator_dry_run.py tests/test_lm_translator_output_writeback.py --basetemp=.pytest-tmp\pr41-lm -o cache_dir=.pytest-cache\pr41-lm`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr41-full -o cache_dir=.pytest-cache\pr41-full`

---

## Rejected approaches
1) 試過：把 `lm_translator_shared.py` 直接砍掉，所有 caller 一次改成新模組路徑。
2) 為什麼放棄：雖然理論上最乾淨，但這會把 shared boundary refactor 變成 migration PR；若任何一個 caller 漏改，錯會散在多顆 PR。
3) 最終改採：先保留 shared façade，等後面邊界穩定後再用 PR57 安全收尾。

---

## Not included in this PR
- 不改 `lm_translator_main.py` 的 engine 邏輯。
- 不改 public result dict 格式。
- 不改 cache rule 實際判定規則。

---

## Next step
- PR42 進入 `lang_merge_content.py`，把 lang merge 的內容處理層切開。
