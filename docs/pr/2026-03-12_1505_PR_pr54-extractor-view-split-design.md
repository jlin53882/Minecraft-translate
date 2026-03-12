# PR54 設計稿：`app/views/extractor_view.py` 結構拆分

## Summary
PR54 要把 `extractor_view.py` 從『設定卡 + logs + extraction + preview dialog 都擠一起』切成可維護 UI。這顆 PR 不動 core extraction 邏輯，完全建立在 PR46 的 jar processor 分層已完成之上。

---

## Phase 0 盤點
- 目前 `app/views/extractor_view.py` 約 805 行。
- 檔案內同時有 settings card、logs card、directory picking、extraction flow、preview flow、兩套 dialog helper（新舊版本並存痕跡）。
- preview/report 動線依賴 `jar_processor.py`，代表 core 不先整理好，UI 這顆很難拆。
- PR51 已先要求補 characterization tests，PR54 才能安全切結構。

---

## 設計範圍
- 新增 `app/views/extractor/extractor_state.py`，集中 mode/path/output/status/log/stats 狀態。
- 新增 `app/views/extractor/extractor_actions.py`，集中 extraction start、preview start、poller lifecycle、summary 顯示。
- 新增 `app/views/extractor/extractor_panels.py`，集中 settings/logs/rendering helper。
- 把 preview dialog helper 收斂成單一路徑，避免舊版 `_show_preview_dialog_result()` / v2 兩套並存。
- `ExtractorView` 主類別只保留 file picker glue、panel 組裝與 state/action 連線。

---

## Validation checklist
- [ ] `rg -n "def _build_settings_card|def _build_logs_card|def start_extraction|def show_preview|def _show_preview_dialog_result|def _show_preview_dialog_result_v2" app/views/extractor_view.py app/views/extractor --glob "*.py"`
- [ ] `uv run pytest -q tests/test_extractor_view_characterization.py tests/test_jar_processor_find.py tests/test_jar_processor_extract.py tests/test_jar_preview_report.py --basetemp=.pytest-tmp\pr54 -o cache_dir=.pytest-cache\pr54`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr54-full -o cache_dir=.pytest-cache\pr54-full`

---

## Rejected approaches
1) 試過：先把 preview dialog 重寫成新 UI，再說結構拆分。
2) 為什麼放棄：那會把『結構整理』跟『行為/UI 改版』混成一顆 PR，違反整輪的單一責任原則。
3) 最終改採：只抽 state/actions/panels；preview UX 長相先維持。

---

## Not included in this PR
- 不改 extraction / preview 的外觀。
- 不修改 jar core 行為。
- 不新增新模式。

---

## Next step
- PR55 處理 `translation_view.py` 的 form / task glue / rendering 混裝。
