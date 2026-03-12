# PR53 設計稿：`app/views/cache_view.py` 大型 UI 模組拆分

## Summary
PR53 是整輪 UI 重構裡最難的一顆。`cache_view.py` 已經到 2500+ 行，而且同時包含 state、action、panel、history window、query/shard workspace。這顆 PR 不能硬切，必須沿著既有 `app/views/cache_manager/` 子模組把責任慢慢拉出去。

---

## Phase 0 盤點
- 目前 `app/views/cache_view.py` 約 2519 行，是整個 repo 最大檔。
- repo 已存在 `app/views/cache_manager/` 子模組與 cache view 測試：`tests/test_cache_controller.py`、`tests/test_cache_presenter.py`、`tests/test_cache_view_features.py`、`tests/test_cache_view_monkeypatch_integration.py`、`tests/test_cache_view_state_gate.py`。
- `cache_view.py` 已有部分總覽區抽離經驗，代表這顆 PR 可以沿著既有 seam 往外拉，而不是從零開始。
- 高風險區塊包括：query/search、shard workspace、history window、ui_busy state 與 action_id 競態。

---

## 設計範圍
- 新增 `app/views/cache_manager/cache_state.py`，集中 UI state、page 狀態、history selection、pagination 狀態。
- 新增 `app/views/cache_manager/cache_actions.py`，集中 reload/save/search/rebuild/history apply 等 action handlers。
- 新增 `app/views/cache_manager/cache_history_panel.py` 與 `cache_query_panel.py`，把 query/shard/history panel builders 從主類別拔出。
- `CacheView` 類別只保留 page lifecycle、依賴注入、子 panel 組裝與少量 glue。
- 延用既有 cache view tests，再新增 panel wiring / state transition focused tests；任何拆分都以這些測試作防撞欄。

---

## Validation checklist
- [ ] `rg -n "class CacheView|def _render_query_results|def _render_query_history|def _render_shard_history|def _run_action|def _load_overview" app/views/cache_view.py app/views/cache_manager --glob "*.py"`
- [ ] `uv run pytest -q tests/test_cache_controller.py tests/test_cache_presenter.py tests/test_cache_view_features.py tests/test_cache_view_monkeypatch_integration.py tests/test_cache_view_state_gate.py --basetemp=.pytest-tmp\pr53 -o cache_dir=.pytest-cache\pr53`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr53-full -o cache_dir=.pytest-cache\pr53-full`

---

## Rejected approaches
1) 試過：一次把 `CacheView` 改成 controller + presenter 雙物件主導，主 view 幾乎清空。
2) 為什麼放棄：理論上很漂亮，但風險極高；這顆檔案行為太多，一次改架構等於直接跟 UI 現況對賭。
3) 最終改採：沿既有 `cache_manager` 子模組漸進拆分，先把可測 panel / state / action 拉出去。

---

## Not included in this PR
- 不改 cache UI 行為。
- 不改 service contract。
- 不順手重畫介面。

---

## Next step
- PR54 再拆 `extractor_view.py`，但那顆要以前面 PR46 的 jar core 分層為前提。
