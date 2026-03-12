# PR31 Phase 1 執行 + Validation 回報（停在 commit 前）

> 狀態：Phase 1 已完成，Validation 已跑完，**尚未 commit/push**。

## 本次實作內容（non-UI）

### 新增檔案
- `translation_tool/plugins/shared/__init__.py`
- `translation_tool/plugins/shared/json_io.py`
- `translation_tool/plugins/shared/lang_path_rules.py`
- `tests/test_plugins_shared_helpers.py`

### 修改檔案
- `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py`
- `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py`

### 變更重點
- FTB/KubeJS 兩邊重複 helper 已抽到 `plugins/shared/*`
- 原 plugin 內 helper 定義已移除，改為 import shared
- 依你要求，先補最小行為測試（路徑規則 + lang segment + JSON 讀寫）再進行 Validation

---

## Validation checklist 實際輸出

### 1) import smoke
```text
> uv run python -c "from translation_tool.plugins.ftbquests.ftbquests_lmtranslator import translate_ftb_pending_to_zh_tw; print('ftb-import-ok')"
ftb-import-ok

> uv run python -c "from translation_tool.plugins.kubejs.kubejs_tooltip_lmtranslator import translate_kubejs_pending_to_zh_tw; print('kjs-import-ok')"
kjs-import-ok
```

### 2) 新增測試（最小補法）
```text
> uv run pytest -q tests/test_plugins_shared_helpers.py --basetemp=.pytest-tmp\pr31-newtests -o cache_dir=.pytest-cache\pr31-newtests
..........                                                               [100%]
10 passed in 0.05s
```

### 3) 全量測試
```text
> uv run pytest -q --basetemp=.pytest-tmp\pr31-phase1 -o cache_dir=.pytest-cache\pr31-phase1
..................................................                       [100%]
50 passed in 0.93s
```

### 4) helper 定義位置驗證
```text
> rg -n "^def (read_json_dict|write_json_dict|collect_json_files|should_rename_to_zh_tw|is_lang_code_segment|replace_lang_folder_with_zh_tw|compute_output_path)\(" translation_tool/plugins/ftbquests translation_tool/plugins/kubejs translation_tool/plugins/shared
translation_tool/plugins/shared\lang_path_rules.py:6:def should_rename_to_zh_tw(...)
translation_tool/plugins/shared\lang_path_rules.py:17:def is_lang_code_segment(...)
translation_tool/plugins/shared\lang_path_rules.py:28:def replace_lang_folder_with_zh_tw(...)
translation_tool/plugins/shared\lang_path_rules.py:40:def compute_output_path(...)
translation_tool/plugins/shared\json_io.py:8:def read_json_dict(...)
translation_tool/plugins/shared\json_io.py:17:def write_json_dict(...)
translation_tool/plugins/shared\json_io.py:24:def collect_json_files(...)
```

### 5) 外部直接 import 舊 helper 位置（repo 內）
```text
> rg -n "from\s+translation_tool\.plugins\.(ftbquests\.ftbquests_lmtranslator|kubejs\.kubejs_tooltip_lmtranslator)\s+import\s+.*(read_json_dict|write_json_dict|collect_json_files|should_rename_to_zh_tw|is_lang_code_segment|replace_lang_folder_with_zh_tw|compute_output_path)" . --glob "*.py" --glob "!backups/**" --glob "!docs/**"
(no output, exit code 1)
```

---

## 目前工作樹狀態（未 commit）
```text
M translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py
M translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py
?? translation_tool/plugins/shared/
?? tests/test_plugins_shared_helpers.py
...（另有 PR31~PR37 設計稿與 Phase0 報告未追蹤）
```

---

## 需要你確認
1. 這版 Validation 輸出是否可接受？
2. 可否進入下一步：整理 PR31 相關檔案後 commit/push？
