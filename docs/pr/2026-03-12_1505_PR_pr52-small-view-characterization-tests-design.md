# PR52 設計稿：小型 view characterization tests（第二批）

## Summary
PR52 的目標是把剩下仍活躍的 view 至少補到『不會完全裸奔』。這顆 PR 不追求完整 UI coverage，而是針對 service glue、主要按鈕與基本狀態切換建立最低限度保護。

---

## Phase 0 盤點
- 目前 `merge_view.py` 約 242 行、`lm_view.py` 約 208 行、`icon_preview_view.py` 約 331 行、`lookup_view.py` 約 117 行、`bundler_view.py` 約 135 行。
- 這些 view 雖然比 PR51 的四支小，但仍有 thread/poller、file picker、service glue、資料載入等容易壞的互動。
- repo 現況幾乎沒有這幾支 view 的直接測試。
- PR52 做完之後，整個活躍 UI 才算真的有基本護欄。

---

## 設計範圍
- 新增 `tests/test_merge_view_characterization.py`、`tests/test_lm_view_characterization.py`、`tests/test_icon_preview_view_characterization.py`、`tests/test_lookup_view_characterization.py`、`tests/test_bundler_view_characterization.py`。
- 每支測試至少鎖：初始化成功、主要 action button wiring、service/worker 啟動點、最小 status/log 變化。
- 對 `icon_preview_view.py` 額外鎖 page bar / mod detail / save current zh 的核心操作流。
- 對 `lookup_view.py`、`bundler_view.py` 則優先保護 worker 啟動與錯誤提示。

---

## Validation checklist
- [ ] `uv run pytest -q tests/test_merge_view_characterization.py tests/test_lm_view_characterization.py tests/test_icon_preview_view_characterization.py tests/test_lookup_view_characterization.py tests/test_bundler_view_characterization.py tests/test_ui_refactor_guard.py --basetemp=.pytest-tmp\pr52 -o cache_dir=.pytest-cache\pr52`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr52-full -o cache_dir=.pytest-cache\pr52-full`

---

## Rejected approaches
1) 試過：把小 view 測試跟 PR51 合併成一顆大測試 PR。
2) 為什麼放棄：那顆會變成超胖測試 PR，一旦哪支 view fixture 沒穩，整包 review 成本會很差。
3) 最終改採：切成兩顆：先大 view、再小 view，風險更好控制。

---

## Not included in this PR
- 不包含 `cache_view.py`。
- 不包含 `qc_view.py`。
- 不做 UI 結構重整。

---

## Next step
- PR53 才正式拆最大的 `cache_view.py`。
