# PR32 Phase 0 盤點報告（停在 Phase 0，未進入 Phase 1）

## 結論
- `_strip_fmt` / `is_already_zh` 目前僅在兩個 plugin 檔案內定義與使用：
  - `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py`
  - `translation_tool/plugins/md/md_lmtranslator.py`
- repo 內無其他模組直接 `from ... import _strip_fmt / is_already_zh`
- 未發現 `getattr(...)` 動態呼叫這兩個符號
- baseline 測試：`50 passed`

---

## Phase 0 checklist

### 1) caller 盤點（source code）
命令：
- `rg -n "_strip_fmt|is_already_zh" <repo> --glob "*.py" --glob "!backups/**" --glob "!docs/**"`

結果：
- 命中只在：
  - `ftbquests_lmtranslator.py`
  - `md_lmtranslator.py`

### 2) 外部直接 import 盤點
命令：
- `rg -n "from translation_tool.plugins.(ftbquests.ftbquests_lmtranslator|md.md_lmtranslator) import .*(_strip_fmt|is_already_zh)" ...`

結果：
- 無命中（repo 內無外部直接 import）

### 3) 動態呼叫風險
命令：
- `rg -n "getattr\(.*(_strip_fmt|is_already_zh)" ...`
- 補充固定字串檢索（單引號/雙引號）

結果：
- 無命中（未見動態呼叫）

### 4) guard test baseline
命令：
- `uv run pytest -q --basetemp=.pytest-tmp\pr32-phase0 -o cache_dir=.pytest-cache\pr32-phase0`

結果：
- `50 passed in 0.91s`

---

## 風險提示（進 Phase 1 前）
- 雖無外部 caller，但 `_strip_fmt/is_already_zh` 屬文字判定核心，若抽離時邏輯微改會影響「跳過翻譯」比例。
- 建議在 PR32 內至少補：
  1. `_strip_fmt` 樣本測試
  2. `is_already_zh` 樣本測試
  3. FTB/MD import smoke

---

## 目前停點
- ✅ PR32 Phase 0 完成
- ⛔ 未進入 Phase 1（尚未改動 PR32 目標程式碼）
