# PR51：large view characterization tests

## Summary
這顆 PR 完全不動 UI 行為，只先替四支大型 view 補上 characterization tests。沒有這層護欄，PR54~56 幾乎就是盲飛。這次先鎖住最常用、也最容易在 refactor 時誤傷的四支：translation / extractor / config / rules。

---

## Phase 1 完成清單
- [x] 做了：新增 `tests/test_translation_view_characterization.py`
- [x] 做了：新增 `tests/test_extractor_view_characterization.py`
- [x] 做了：新增 `tests/test_config_view_characterization.py`
- [x] 做了：新增 `tests/test_rules_view_characterization.py`
- [x] 做了：保留 `tests/test_ui_refactor_guard.py`、`tests/test_view_wrapper.py` 一起跑，確認共用層 guard 沒被新測試假設打爆。
- [ ] 未做：cache_view / qc_view（原因：依主計畫，這兩支留給後續獨立處理）。

---

## What was done

### 1. translation_view characterization
新增 `tests/test_translation_view_characterization.py`，先鎖住：
- 初始 tab 數量 = 3
- 共用 status/progress panel 初始化
- `_run_ftb(dry_run=True)` 會把當前 checkbox/switch 狀態正確送進 service
- `_reset_md_inputs()` 會恢復預設值並附帶 UI log

### 2. extractor_view characterization
新增 `tests/test_extractor_view_characterization.py`，先鎖住：
- Lang / Book 提取按鈕與 preview 按鈕存在
- 清除輸出路徑時會附帶 `[系統] 已清除輸出路徑` log
- `_update_stats_from_log()` 對 success/warning/failure 的統計累加邏輯

### 3. config_view characterization
新增 `tests/test_config_view_characterization.py`，先鎖住：
- `load_config()` 會把 models / keys 載回 UI
- add/remove model row 的基本可觀測行為
- `save_config_clicked()` 會把 key/model row 正確寫回 config 結構

### 4. rules_view characterization
新增 `tests/test_rules_view_characterization.py`，先鎖住：
- 初次載入會填入 `all_rules_data`
- 搜尋會生成 `search_results`
- duplicate rule validation 的 observable behavior
- add row 會把分頁狀態推到最後一頁（此案例只有 1 頁）

---

## Important findings
- 這顆 PR 的核心不是「把測試寫得多漂亮」，而是先把四支大 view 的最小 observable contract 鎖住。
- 測試策略全部走 characterization first：鎖現在的行為，而不是先評價 UI 夠不夠理想。
- targeted / full pytest 都綠，但有 Flet `Text.style` 的既有 deprecation warnings；這是舊行為噪音，不是本顆新增問題。

---

## Validation checklist
- [x] `uv run pytest -q tests/test_translation_view_characterization.py tests/test_extractor_view_characterization.py tests/test_config_view_characterization.py tests/test_rules_view_characterization.py tests/test_ui_refactor_guard.py tests/test_view_wrapper.py --basetemp=.pytest-tmp\pr51 -o cache_dir=.pytest-cache\pr51`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr51-full -o cache_dir=.pytest-cache\pr51-full`

## Test result
```text
$ uv run pytest -q tests/test_translation_view_characterization.py tests/test_extractor_view_characterization.py tests/test_config_view_characterization.py tests/test_rules_view_characterization.py tests/test_ui_refactor_guard.py tests/test_view_wrapper.py --basetemp=.pytest-tmp\pr51 -o cache_dir=.pytest-cache\pr51
....................                                                     [100%]
20 passed, 25 warnings in 0.53s

$ uv run pytest -q --basetemp=.pytest-tmp\pr51-full -o cache_dir=.pytest-cache\pr51-full
........................................................................ [ 50%]
........................................................................ [100%]
144 passed, 25 warnings in 1.54s
```

---

## Rejected approaches
1) 試過：先拆 view，拆完再補測試。
   - 為什麼放棄：這就是賭局路線；一旦 UI 行為漂掉，根本沒 baseline 可比。
   - 最終改採：先補 characterization tests，再讓後面拆分可以保舊行為。

2) 試過：只補 shared/ui refactor guard，不直接碰四支大 view。
   - 為什麼放棄：那只能保共用層，保不了 view 自己的 service glue / reset / pagination / stats 行為。
   - 最終改採：直接替四支活躍大 view 各補最小 characterization tests。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改任何 UI 行為
- 沒有調整 layout
- 沒有重命名 controls
- 沒有處理 cache_view 與 qc_view

---

## Next step

### PR52
- 補齊其餘活躍 view 的最低限度護欄。
- 等 view 測試護城河補得比較完整，再下刀拆 view 會穩很多。
