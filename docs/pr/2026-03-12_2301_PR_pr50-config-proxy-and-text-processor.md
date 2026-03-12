# PR50：config proxy and text processor

## Summary
這顆 PR 不硬砍 `LazyConfigProxy`，而是先把 config 讀取責任盤清楚，再把 `text_processor.py` 這種高價值工作模組改成顯式 helper。目標是降低隱式設定依賴，同時保住既有相容 seam。

---

## Phase 1 完成清單
- [x] 做了：盤點目前 repo 內仍依賴 `load_config()` / `LazyConfigProxy` / `config` 舊介面的 caller。
- [x] 做了：新增 `translation_tool/utils/config_access.py`，提供顯式 `get_runtime_config()` / `resolve_runtime_path()` helper。
- [x] 做了：調整 `text_processor.py`，改走顯式 path helper，不再直接依賴 config proxy。
- [x] 做了：保留 `text_processor.resolve_project_path` 作 legacy seam，內部導向新 helper。
- [x] 做了：新增 focused tests：`test_config_proxy_compat.py`、`test_text_processor_config_resolution.py`。
- [ ] 未做：刪除 `config = LazyConfigProxy()`（原因：這顆先做 inventory + 高價值 caller 收斂，不做硬切 migration）。

---

## What was done

### 1. 先做 inventory，不亂砍
盤點結果很清楚：
- repo 仍有不少 `load_config()` caller，這是正常顯式依賴
- 真正 legacy seam 主要是：
  - `config = LazyConfigProxy()`
  - 少數直接 `from config_manager import config`
  - `text_processor` 原本直接倚賴舊式 path 解析習慣

所以這顆 PR 的正解不是直接刪 proxy，而是先把高價值模組收斂乾淨。

### 2. 新增 config_access 顯式 helper
新增 `translation_tool/utils/config_access.py`：
- `get_runtime_config()`
- `resolve_runtime_path()`

這層的目的是讓新 caller 有明確入口，不要再養出更多對 proxy 的隱式依賴。

### 3. 調整 text_processor，改走顯式 path helper
`translation_tool/utils/text_processor.py` 現在：
- 以 `config_access.resolve_runtime_path()` 作為底層 path 解析來源
- 新增 `_resolve_rules_path()` 包裝 replace rules path 解析
- `load_replace_rules()` / `save_replace_rules()` / `load_custom_translations()` 都改走顯式 helper

### 4. 保留 legacy seam，不打爆既有測試/patch 點
這顆又踩到一個相容點：
- `tests/test_path_resolution.py` 仍會 monkeypatch `text_processor.resolve_project_path`
- 如果把這個 symbol 直接拿掉，功能沒壞，但既有 guard/compat test 會炸

修正後：
- `text_processor` 重新暴露 `resolve_project_path`
- 但內部已導向新 helper `resolve_runtime_path`

也就是說：**外面看起來還活著，裡面其實已經換新心臟。**

---

## Important findings
- PR50 的關鍵不是「能不能砍 proxy」，而是「哪些 seam 其實還有人在用」。
- 這次最值得保留的經驗是：很多老 seam 已經不是 runtime 真依賴，而是 test / monkeypatch 依賴；硬刪只會讓 refactor 成本無端升高。
- 所以這顆 PR 走的是：先把高價值 caller 改成顯式 helper，再把 legacy seam 留成薄相容層。

---

## Validation checklist
- [x] `rg -n "from .*config_manager import config|config_manager\.config|LazyConfigProxy|load_config\(" translation_tool app --glob "*.py"`
- [x] `uv run pytest -q tests/test_config_proxy_compat.py tests/test_text_processor_config_resolution.py tests/test_path_resolution.py --basetemp=.pytest-tmp\pr50 -o cache_dir=.pytest-cache\pr50`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr50-full -o cache_dir=.pytest-cache\pr50-full`

## Test result
```text
$ rg -n "from .*config_manager import config|config_manager\.config|LazyConfigProxy|load_config\(" translation_tool app --glob "*.py"
...（完成 inventory，確認 proxy 仍存在且 caller 分布可追蹤）

$ uv run pytest -q tests/test_config_proxy_compat.py tests/test_text_processor_config_resolution.py tests/test_path_resolution.py --basetemp=.pytest-tmp\pr50 -o cache_dir=.pytest-cache\pr50
......                                                                   [100%]
6 passed in 0.18s

$ uv run pytest -q --basetemp=.pytest-tmp\pr50-full -o cache_dir=.pytest-cache\pr50-full
........................................................................ [ 54%]
...........................................................              [100%]
131 passed in 1.54s
```

---

## Rejected approaches
1) 試過：直接刪掉 `config = LazyConfigProxy()`，逼所有 caller 一次改完。
   - 為什麼放棄：這做法太硬，任何漏網之魚都會變 runtime import error。
   - 最終改採：先盤點、先替換高價值 caller，再把 proxy 限縮成明確 legacy seam。

2) 試過：把 `text_processor.resolve_project_path` 直接拿掉，只留新 helper。
   - 為什麼放棄：repo 內既有 path resolution tests 仍在 monkeypatch 這個 seam，直接拿掉只會打爆相容契約。
   - 最終改採：保留 `resolve_project_path` 名稱，但導向新 helper `resolve_runtime_path`。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 config.json schema
- 沒有改 logging config 格式
- 沒有順手處理 unrelated path bugs
- 沒有刪除 LazyConfigProxy

---

## Next step

### PR51
- 進入 UI 前置測試工程，先補大 view characterization tests。
- 等 views 的 guard 補穩後，再做後續 view split 才不會一直踩暗雷。
