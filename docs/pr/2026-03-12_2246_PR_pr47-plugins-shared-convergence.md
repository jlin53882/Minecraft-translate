# PR47：plugins shared convergence

## Summary
這顆 PR 不做大搬家，而是把前面幾顆 pipeline 重構後已經穩定的共用規則，正式收斂成 `translation_tool/plugins/shared/` 的 public API。重點不是讓 shared 變超大，而是讓它開始比較像唯一真相來源，而不是「存在，但大家還各抓各的」。

---

## Phase 1 完成清單
- [x] 做了：盤點 FTB / KubeJS / MD 對 shared helper 的實際使用情況。
- [x] 做了：補 `translation_tool/plugins/shared/__init__.py` 的 public API re-export 與 `__all__`。
- [x] 做了：新增 focused tests：`tests/test_plugins_shared_lang_rules.py`、`tests/test_plugins_shared_json_io.py`。
- [x] 做了：保留既有 `tests/test_plugins_shared_helpers.py` 作 baseline，確認新舊入口都穩。
- [ ] 未做：把 pipeline-specific 的 cache/dry-run loop 硬搬進 shared（原因：那不是全域規則，現在搬只會把 shared 變神檔）。

---

## What was done

### 1. 做 shared inventory，先確認哪些真的是 shared
盤點結果很明確：
- FTB / KubeJS 已經實際依賴 `plugins/shared/json_io.py`、`lang_path_rules.py`、`lang_text_rules.py`
- MD 主要依賴 `is_already_zh()`
- 真正跨 plugin 重複且穩定的，是 JSON I/O / path rename / already-zh 判定
- cache split / translate loop 雖然也共用，但那一層現在應該留在 `lm_translator_shared`，不是再搬進 `plugins/shared`

### 2. 把 shared 變成明確 public API
更新 `translation_tool/plugins/shared/__init__.py`：
- re-export：`collect_json_files`、`read_json_dict`、`write_json_dict`
- re-export：`compute_output_path`、`is_lang_code_segment`、`replace_lang_folder_with_zh_tw`、`should_rename_to_zh_tw`
- re-export：`is_already_zh`
- 用 `__all__` 明確標出 public surface

這樣 caller 可以開始依賴 shared public API，而不是永遠直鑽子模組。

### 3. 補 focused tests，鎖 shared contract
新增：
- `tests/test_plugins_shared_lang_rules.py`
- `tests/test_plugins_shared_json_io.py`

再加上既有 `tests/test_plugins_shared_helpers.py` 一起跑，確保：
- 子模組 API 沒壞
- public shared API 也穩

---

## Important findings
- PR47 最重要的判斷不是「能不能多搬一點」，而是「哪些東西現在不該搬」。
- 前面 PR43~46 拆出來的 helper，有些看起來很像 shared，但其實是 pipeline-specific orchestration；這次如果硬搬，只會把 `plugins/shared` 變成新的垃圾桶。
- 所以這顆 PR 的正解是：**補 shared API、補測試、補清楚邊界；不要貪心。**

---

## Validation checklist
- [x] `rg -n "translation_tool\.plugins\.shared|is_already_zh|compute_output_path|read_json_dict|write_json_dict|collect_json_files" translation_tool tests --glob "*.py"`
- [x] `uv run pytest -q tests/test_plugins_shared_helpers.py tests/test_plugins_shared_lang_rules.py tests/test_plugins_shared_json_io.py --basetemp=.pytest-tmp\pr47 -o cache_dir=.pytest-cache\pr47`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr47-full -o cache_dir=.pytest-cache\pr47-full`

## Test result
```text
$ rg -n "translation_tool\.plugins\.shared|is_already_zh|compute_output_path|read_json_dict|write_json_dict|collect_json_files" translation_tool tests --glob "*.py"
...（inventory 命中 FTB / KubeJS / MD plugin 與 shared/tests，確認 shared 已實際被使用）

$ uv run pytest -q tests/test_plugins_shared_helpers.py tests/test_plugins_shared_lang_rules.py tests/test_plugins_shared_json_io.py --basetemp=.pytest-tmp\pr47 -o cache_dir=.pytest-cache\pr47
.........................                                                [100%]
25 passed in 0.08s

$ uv run pytest -q --basetemp=.pytest-tmp\pr47-full -o cache_dir=.pytest-cache\pr47-full
........................................................................ [ 59%]
..................................................                       [100%]
122 passed in 1.53s
```

---

## Rejected approaches
1) 試過：把所有 plugin helper 無差別往 shared 搬，先集中再說。
   - 為什麼放棄：這種做法很容易把 shared 變成新神檔，而且會把 pipeline-specific 邏輯錯搬成 global rule。
   - 最終改採：只收真正跨 plugin 重複且已被前面 PR 驗證穩定的規則。

2) 試過：只靠 existing helper tests，不補 public API tests。
   - 為什麼放棄：那樣只能保證子模組活著，不能保證 shared public surface 沒漂移。
   - 最終改採：補 public shared API focused tests，讓 `__init__` / `__all__` 也被保護。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有動 UI
- 沒有改 translation tool core 主入口
- 沒有在這顆 PR 做 dead code 實刪

---

## Next step

### PR48
- 開始整理 app service lifecycle，讓 UI service wrapper 也跟著瘦下來。
- shared 現在比較像樣了，後面再做 cleanup 才不容易亂砍。
