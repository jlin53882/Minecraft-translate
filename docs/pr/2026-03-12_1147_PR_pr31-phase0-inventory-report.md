# PR31 Phase 0 盤點報告（停在 Phase 0，未進入 Phase 1）

## 結論（先給你）
- 目前 7 個目標 helper 的 caller 全在兩個檔案內（FTB / KubeJS plugin 自用）
- repo 內沒有其他模組直接 `from ... import <helper>` 這些函數
- 沒有偵測到 `getattr(..., "func_name")` 這種動態呼叫
- guard test 現況：**沒有**直接覆蓋 FTB/KubeJS plugin helper 的測試（只有 import smoke + 全量 pytest）

---

## Phase 0 checklist

### 1) 全 repo caller 盤點（source code）
命令：
- `rg -n "read_json_dict|write_json_dict|collect_json_files|should_rename_to_zh_tw|is_lang_code_segment|replace_lang_folder_with_zh_tw|compute_output_path" <repo> --glob "*.py" --glob "!backups/**"`

結果摘要：
- `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py`
- `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py`
以外沒有其他 source caller。

### 2) 是否有外部直接 import 舊 helper 位置
命令：
- `rg -n "from translation_tool.plugins.(ftbquests.ftbquests_lmtranslator|kubejs.kubejs_tooltip_lmtranslator) import (read_json_dict|write_json_dict|collect_json_files|should_rename_to_zh_tw|is_lang_code_segment|replace_lang_folder_with_zh_tw|compute_output_path)" <repo> --glob "*.py" --glob "!backups/**" --glob "!docs/**"`

結果：
- 無命中（repo 內沒有外部直接 import 這些 helper）

### 3) 動態呼叫風險檢查（getattr / 字串）
命令：
- `rg -n "getattr\(.*(read_json_dict|write_json_dict|collect_json_files|should_rename_to_zh_tw|is_lang_code_segment|replace_lang_folder_with_zh_tw|compute_output_path)" <repo> --glob "*.py" --glob "!backups/**" --glob "!docs/**"`
- 固定字串檢查（單引號版本）：`'read_json_dict' ... 'compute_output_path'`

結果：
- 無命中（未見動態字串呼叫）

### 4) 舊 -> 新 import 對照（Phase 1 預備）
建議對照：
- `read_json_dict` -> `translation_tool.plugins.shared.json_io.read_json_dict`
- `write_json_dict` -> `translation_tool.plugins.shared.json_io.write_json_dict`
- `collect_json_files` -> `translation_tool.plugins.shared.json_io.collect_json_files`
- `should_rename_to_zh_tw` -> `translation_tool.plugins.shared.lang_path_rules.should_rename_to_zh_tw`
- `is_lang_code_segment` -> `translation_tool.plugins.shared.lang_path_rules.is_lang_code_segment`
- `replace_lang_folder_with_zh_tw` -> `translation_tool.plugins.shared.lang_path_rules.replace_lang_folder_with_zh_tw`
- `compute_output_path` -> `translation_tool.plugins.shared.lang_path_rules.compute_output_path`

### 5) guard tests 現況
- 測試檔清單無 FTB/KubeJS plugin helper 專用測試
- 已跑的 baseline：
  - `uv run pytest -q --basetemp=.pytest-tmp\pr31-phase0 -o cache_dir=.pytest-cache\pr31-phase0`
  - 結果：`40 passed in 0.92s`
- import smoke：
  - `from translation_tool.plugins.ftbquests.ftbquests_lmtranslator import translate_ftb_pending_to_zh_tw` -> OK
  - `from translation_tool.plugins.kubejs.kubejs_tooltip_lmtranslator import translate_kubejs_pending_to_zh_tw` -> OK

---

## 目前停點
- ✅ PR31 Phase 0 已完成
- ⛔ 尚未進入 Phase 1（未改任何目標程式碼）

## 建議你確認後再放行
1. 是否同意「先做結構抽離、不補 helper 單元測試到 PR31」
2. 或要我在 PR31 直接加最小 guard tests（我建議加 2~3 個）再進 Phase 1
