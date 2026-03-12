# PR31 設計稿：plugins shared JSON/path helpers（FTB + KubeJS）

## Summary
抽離 FTB 與 KubeJS LM translator 的重複 helper 到 `translation_tool/plugins/shared/`，只做結構去重，不改翻譯行為。

---

## Phase 0 盤點（必做）
- [ ] 全 repo 檢索 `read_json_dict|write_json_dict|collect_json_files|should_rename_to_zh_tw|compute_output_path` caller
- [ ] 確認沒有外部模組直接 import 目前 plugin 內 helper（至少 repo 內）
- [ ] 列出舊 -> 新 import 對照表
- [ ] guard tests 清單確認（至少全量 pytest + 兩個 plugin import smoke）

---

## Phase 1 設計範圍
### 新增
- `translation_tool/plugins/shared/json_io.py`
- `translation_tool/plugins/shared/lang_path_rules.py`

### 修改
- `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py`
- `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py`

### 搬移函數
- JSON I/O：`read_json_dict`, `write_json_dict`, `collect_json_files`
- 路徑規則：`should_rename_to_zh_tw`, `is_lang_code_segment`, `replace_lang_folder_with_zh_tw`, `compute_output_path`

---

## Out of scope
- 不改翻譯策略與 cache 策略
- 不改 UI

---

## 刪除/移除/替換說明
- **刪除/替換項目**：兩個 plugin 內重複 helper 定義
- **為什麼改**：重複維護成本高，規則易分叉
- **現況 caller**：FTB/KubeJS 兩個 plugin 內部自用（Phase 0 會再確認）
- **替代路徑**：`plugins/shared/json_io.py`、`plugins/shared/lang_path_rules.py`
- **風險**：路徑語系替換規則若偏移，輸出檔名可能變化
- **驗證依據**：import smoke + 全量 pytest + （可選）固定樣本輸出比對

---

## Validation checklist
- [ ] `rg -n "read_json_dict|write_json_dict|collect_json_files|should_rename_to_zh_tw|compute_output_path" . --no-ignore`
- [ ] `uv run python -c "from translation_tool.plugins.ftbquests.ftbquests_lmtranslator import translate_ftb_pending_to_zh_tw; print('ok')"`
- [ ] `uv run python -c "from translation_tool.plugins.kubejs.kubejs_tooltip_lmtranslator import translate_kubejs_pending_to_zh_tw; print('ok')"`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr31 -o cache_dir=.pytest-cache\pr31`

---

## Rejected approaches
1) **方案**：先不抽 shared，只在兩個 plugin 各自微調註解。  
   **放棄原因**：無法解決重複維護問題。  
2) **方案**：一次把 MD plugin 也一起併進 PR31。  
   **放棄原因**：範圍過大，不利低風險落地。  
3) **最終採用**：先做 FTB+KubeJS，下一顆 PR32 再處理文字判定共用。

---

## Next
PR32 抽離 `_strip_fmt` / `is_already_zh` 類文字判定 helper。
