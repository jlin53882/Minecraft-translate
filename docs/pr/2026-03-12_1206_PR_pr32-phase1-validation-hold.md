# PR32 Phase 1 執行 + Validation 回報（停在 commit 前）

> 狀態：Phase 1 已完成，Validation 已跑完，**尚未 commit/push**。

## 本次實作內容（non-UI）

### 新增檔案
- `translation_tool/plugins/shared/lang_text_rules.py`

### 修改檔案
- `translation_tool/plugins/shared/__init__.py`
- `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py`
- `translation_tool/plugins/md/md_lmtranslator.py`
- `tests/test_plugins_shared_helpers.py`

### 變更重點
- 將 `_strip_fmt` / `is_already_zh` 抽到 shared：
  - `translation_tool.plugins.shared.lang_text_rules`
- FTB / MD plugin 改為 import shared helper，舊位置移除定義。
- 依你要求先補最小測試再驗證：
  - `_strip_fmt`：含格式碼樣本剝離
  - `is_already_zh`：中文 True、英文 False、邊界樣本

---

## Validation checklist 實際輸出

### 1) helper 定義位置驗證
```text
> rg -n "^def (_strip_fmt|is_already_zh)\(" translation_tool/plugins/ftbquests translation_tool/plugins/md translation_tool/plugins/shared
translation_tool/plugins/shared/lang_text_rules.py:9:def _strip_fmt(s: str) -> str:
translation_tool/plugins/shared/lang_text_rules.py:14:def is_already_zh(s: str) -> bool:
```

### 2) import smoke
```text
> uv run python -c "from translation_tool.plugins.ftbquests.ftbquests_lmtranslator import translate_ftb_pending_to_zh_tw; print('ftb-import-ok')"
ftb-import-ok

> uv run python -c "from translation_tool.plugins.md.md_lmtranslator import translate_md_pending; print('md-import-ok')"
md-import-ok
```

### 3) 新增/更新的 helper 測試
```text
> uv run pytest -q tests/test_plugins_shared_helpers.py --basetemp=.pytest-tmp\pr32-newtests -o cache_dir=.pytest-cache\pr32-newtests
.................                                                        [100%]
17 passed in 0.07s
```

### 4) 全量測試
```text
> uv run pytest -q --basetemp=.pytest-tmp\pr32-phase1 -o cache_dir=.pytest-cache\pr32-phase1
.........................................................                [100%]
57 passed in 0.98s
```

### 5) 外部直接 import 舊路徑（repo 內）
```text
> rg -n "from\s+translation_tool\.plugins\.(ftbquests\.ftbquests_lmtranslator|md\.md_lmtranslator)\s+import\s+.*(_strip_fmt|is_already_zh)" . --glob "*.py" --glob "!backups/**" --glob "!docs/**"
(no output, exit code 1)
```

---

## 數字對照
- PR32 Phase 0 baseline：`50 passed`
- PR32 Phase 1 後：`57 passed`
- 差異：`+7`（本次新增的 `_strip_fmt/is_already_zh` 相關測試）

---

## 目前停點
- ✅ PR32 Phase 1 與 Validation 完成
- ⛔ 尚未 commit/push（等你確認放行）
