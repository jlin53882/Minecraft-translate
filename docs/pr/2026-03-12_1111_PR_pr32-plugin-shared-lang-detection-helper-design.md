# PR32 設計稿：plugins shared lang detection helpers（FTB + MD）

## Summary
將 FTB/MD 重複的文字判定邏輯抽成共用 helper，避免規則分叉。

---

## Phase 0 盤點（必做）
- [ ] 全 repo 檢索 `_strip_fmt|is_already_zh` 的 caller（含 `--no-ignore`）
- [ ] 確認無外部路徑直接依賴舊函式位置（至少 repo 內）
- [ ] 列出判定邏輯現況差異（若有）
- [ ] guard tests 清單確認

---

## Phase 1 設計範圍
### 新增
- `translation_tool/plugins/shared/lang_text_rules.py`

### 修改
- `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py`
- `translation_tool/plugins/md/md_lmtranslator.py`

### 搬移函數
- `_strip_fmt`
- `is_already_zh`

---

## Out of scope
- 不改 prompt、不改 API key 輪替
- 不改 UI

---

## 刪除/移除/替換說明
- **刪除/替換項目**：FTB/MD 各自重複定義的判定函式
- **為什麼改**：避免同語義規則在兩邊漂移
- **現況 caller**：FTB/MD translator 內部自用（Phase 0 會確認）
- **替代路徑**：`plugins/shared/lang_text_rules.py`
- **風險**：判定邊界改變可能影響「是否送翻譯」比例
- **驗證依據**：固定樣本字串測試 + 全量 pytest

---

## Validation checklist
- [ ] `rg -n "_strip_fmt|is_already_zh" . --no-ignore`
- [ ] `uv run python -c "from translation_tool.plugins.ftbquests.ftbquests_lmtranslator import translate_ftb_pending_to_zh_tw; print('ok')"`
- [ ] `uv run python -c "from translation_tool.plugins.md.md_lmtranslator import translate_md_pending; print('ok')"`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr32 -o cache_dir=.pytest-cache\pr32`

---

## Rejected approaches
1) **方案**：維持各 plugin 自帶判定函式，不抽 shared。  
   **放棄原因**：後續修規則會雙邊漏改。  
2) **方案**：直接併入 PR31 一次做完。  
   **放棄原因**：切分不清，回歸時不易定位。  
3) **最終採用**：獨立 PR32，單點處理文字判定規則共用。

---

## Next
PR33 收斂 pipelines 的 logger 初始化重複。
