# PR55 設計稿：`app/views/translation_view.py` 主翻譯頁拆分

## Summary
PR55 要整理的是主翻譯頁的 form state、task glue、三個 pipeline tab、timer/log/status rendering。這顆 PR 不能碰實際翻譯流程，只能讓 view 更像 view，而不是半個 controller + worker launcher。

---

## Phase 0 盤點
- 目前 `app/views/translation_view.py` 約 607 行。
- 檔案內同時有 FTB / KubeJS / MD 三個 tab builder、path picker、worker thread 啟動、UI timer、status/log reset。
- 這支 view 對 app service contract 很敏感，所以 PR48 的 lifecycle 統一要先完成。
- PR51 已先補 characterization tests，讓 tab / run button / dry-run flow 有 baseline。

---

## 設計範圍
- 新增 `app/views/translation/translation_state.py`，集中三個 tab 的 path/state/status/log。
- 新增 `app/views/translation/translation_actions.py`，集中 `_run_ftb()`、`_run_kjs()`、`_run_md()`、worker 啟動與 timer/poller glue。
- 新增 `app/views/translation/translation_panels.py`，集中 `_build_ftb_tab()`、`_build_kjs_tab()`、`_build_md_tab()` 與共用 path/action row builder。
- `TranslationView` 主類別只保留 file picker 與 state/action/panel 組裝。
- 若有共用 path row / action row 能被其他 view 重用，再評估拉回 `app/ui/`；但這不是 PR55 的預設工作。

---

## Validation checklist
- [ ] `rg -n "def _build_ftb_tab|def _build_kjs_tab|def _build_md_tab|def _run_ftb|def _run_kjs|def _run_md|def _start_ui_timer" app/views/translation_view.py app/views/translation --glob "*.py"`
- [ ] `uv run pytest -q tests/test_translation_view_characterization.py tests/test_pipeline_services_session_lifecycle.py --basetemp=.pytest-tmp\pr55 -o cache_dir=.pytest-cache\pr55`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr55-full -o cache_dir=.pytest-cache\pr55-full`

---

## Rejected approaches
1) 試過：把三個 tab 各自升成三支獨立 View。
2) 為什麼放棄：這會改變 UI 組織層級與切頁概念，等於不是單純 refactor，而是產品/導航層決策。
3) 最終改採：保留同一頁三個 tab 的操作模型，只把內部 state / action / panel 分開。

---

## Not included in this PR
- 不改 tab 版面。
- 不改翻譯 service API。
- 不處理 LM 專屬 view。

---

## Next step
- PR56 收 config/rules 這兩顆表單型 view。
