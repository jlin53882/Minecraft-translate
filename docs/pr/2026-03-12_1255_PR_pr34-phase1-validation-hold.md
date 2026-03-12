# PR34 Phase 1 執行 + Validation 回報（停在 commit 前）

> 狀態：Phase 1 已完成，Validation 已跑完，**尚未 commit/push**。

## 本次採用策略
- 依你確認，採 **選項 1**：
  - PR34 只補 helper guards
  - **不補** `merge_zhcn_to_zhtw_from_zip` generator 本體測試
  - generator 行為驗證留給 PR36 的固定樣本 baseline

---

## 本次實作內容（tests only）

### 新增測試檔
- `tests/test_lm_translator_main_guards.py`
- `tests/test_lang_merger_guards.py`
- `tests/test_cache_manager_api_surface.py`

### 補的 guard 範圍
1. `lm_translator_main.py`
   - `safe_json_loads`
   - `find_lang_json`
   - `extract_translatables`
   - `set_by_path`

2. `lang_merger.py`
   - `collapse_lang_lines`
   - `parse_lang_text`
   - `dump_lang_text`
   - `is_mc_standard_lang_path`
   - `export_filtered_pending`

3. `cache_manager.py` façade 邊界
   - public API surface 存在性
   - `get_cache_dict_ref` live reference 行為
   - 未初始化時 `get_cache_entry/get_cache_dict_ref` 安全返回

---

## Validation checklist 實際輸出

### 1) `lm_translator_main` guards
```text
> uv run pytest -q tests/test_lm_translator_main_guards.py --basetemp=.pytest-tmp\pr34-lm -o cache_dir=.pytest-cache\pr34-lm
.......                                                                  [100%]
7 passed in 0.24s
```

### 2) `lang_merger` guards
```text
> uv run pytest -q tests/test_lang_merger_guards.py --basetemp=.pytest-tmp\pr34-merge -o cache_dir=.pytest-cache\pr34-merge
.........                                                                [100%]
9 passed in 0.06s
```

### 3) `cache_manager` façade guards
```text
> uv run pytest -q tests/test_cache_manager_api_surface.py --basetemp=.pytest-tmp\pr34-cache -o cache_dir=.pytest-cache\pr34-cache
...                                                                      [100%]
3 passed in 0.14s
```

### 4) PR34 新增測試合併跑
```text
> uv run pytest -q tests/test_lm_translator_main_guards.py tests/test_lang_merger_guards.py tests/test_cache_manager_api_surface.py --basetemp=.pytest-tmp\pr34-newtests -o cache_dir=.pytest-cache\pr34-newtests
...................                                                      [100%]
19 passed in 0.26s
```

### 5) 全量測試
```text
> uv run pytest -q --basetemp=.pytest-tmp\pr34-phase1 -o cache_dir=.pytest-cache\pr34-phase1
........................................................................ [ 86%]
...........                                                              [100%]
83 passed in 1.08s
```

### 6) 工作樹檢查
```text
> git diff --name-only
(no output)

> git status --short
?? docs/pr/2026-03-12_1109_PR_pr31-pr37-roadmap-design.md
?? docs/pr/2026-03-12_1113_PR_pr34-non-ui-guard-test-expansion-design.md
?? docs/pr/2026-03-12_1114_PR_pr35-lm-translator-main-module-split-phase1-design.md
?? docs/pr/2026-03-12_1115_PR_pr36-lang-merger-module-split-phase1-design.md
?? docs/pr/2026-03-12_1116_PR_pr37-cache-manager-thin-facade-phase1-design.md
?? docs/pr/2026-03-12_1242_PR_pr34-phase0-inventory-report.md
?? tests/test_cache_manager_api_surface.py
?? tests/test_lang_merger_guards.py
?? tests/test_lm_translator_main_guards.py
```

---

## 數字對照
- PR34 Phase 0 baseline：`64 passed`
- PR34 Phase 1 後：`83 passed`
- 差異：`+19`（正好對應本次新增的 19 個 guard tests）

---

## 補充說明
- `is_mc_standard_lang_path("data/mod/lang/en_us.lang")`
  - 原本直覺可能會想判成 `False`
  - 但現行實作只檢查 `"/lang/"` 與 `.lang` 結尾，因此目前行為是 `True`
  - PR34 是 guard tests，不改行為，所以測試已鎖住現況

---

## 目前停點
- ✅ PR34 Phase 1 與 Validation 完成
- ⛔ 尚未 commit/push（等你確認放行）
