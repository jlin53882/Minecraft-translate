# PR51 設計稿：大型 view characterization tests（第一批）

## Summary
PR51 不做 UI 重構；這顆 PR 純粹是在大型 view 前面架護城河。沒有這顆，PR54~56 幾乎等於盲飛。這批先處理四支最常用、也最容易在 refactor 時誤傷的 view。

---

## Phase 0 盤點
- 目前 `translation_view.py` 約 607 行、`extractor_view.py` 約 805 行、`config_view.py` 約 443 行、`rules_view.py` 約 545 行。
- repo 目前只有 `tests/test_ui_components.py`、`tests/test_ui_refactor_guard.py`、`tests/test_view_wrapper.py` 這種共用層保護，缺這四支 view 的直接 characterization tests。
- 這四支 view 都含有 service glue、thread/poller、input reset 或 load/save 行為，屬於 refactor 時最容易默默壞掉的區。
- 按照主計畫，UI 重構必須晚於 core；那就先把測試補起來。

---

## 設計範圍
- 新增 `tests/test_translation_view_characterization.py`：鎖 tab 初始化、按鈕 wiring、dry-run / real-run 入口、log/status 最小契約。
- 新增 `tests/test_extractor_view_characterization.py`：鎖 extraction mode 切換、preview/start flow、stats/log 累積、snack/error path。
- 新增 `tests/test_config_view_characterization.py`：鎖 load_config、add/remove model/key row、save click 前後的基本資料映射。
- 新增 `tests/test_rules_view_characterization.py`：鎖 reload/save、search/sort、pagination、row validation 的 observable behavior。
- 測試策略全部採 characterization first：先鎖現況，不在本 PR 判定 UI 好不好看。

---

## Validation checklist
- [ ] `uv run pytest -q tests/test_translation_view_characterization.py tests/test_extractor_view_characterization.py tests/test_config_view_characterization.py tests/test_rules_view_characterization.py tests/test_ui_refactor_guard.py tests/test_view_wrapper.py --basetemp=.pytest-tmp\pr51 -o cache_dir=.pytest-cache\pr51`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr51-full -o cache_dir=.pytest-cache\pr51-full`

---

## Rejected approaches
1) 試過：先拆 view，拆完再補測試。
2) 為什麼放棄：這就是典型的賭局路線；一旦 UI 行為漂掉，根本沒 baseline 可比。
3) 最終改採：先補 characterization tests，再讓後面拆分可以保舊行為。

---

## Not included in this PR
- 不改任何 UI 行為。
- 不調整 layout、不重命名 controls。
- 不處理 cache_view 與 qc_view。

---

## Next step
- PR52 補齊其餘活躍 view 的最低限度護欄。
