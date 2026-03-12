# PR35 設計稿：`lm_translator_main.py` 模組切分（Phase 1）

## Summary
將 `translation_tool/core/lm_translator_main.py` 做第一階段切分，目標是先拆責任邊界、維持對外行為。

---

## Phase 0 盤點（必做）
- [ ] 盤點現有 import 依賴圖（誰 import `lm_translator_main`）
- [ ] 盤點可能循環引用點（`lm_translator.py`, shared modules）
- [ ] 明確列出搬移函式清單（逐個符號）
- [ ] 確認對外 API 保留名單（不得破壞）

---

## Phase 1 設計範圍
### 拆分目標（檔名可微調）
- `translation_tool/core/lm_api_client.py`
- `translation_tool/core/lm_response_parser.py`
- `translation_tool/core/translatable_extractor.py`
- `translation_tool/core/translation_path_writer.py`

### 保留原入口
- `lm_translator_main.py` 保留 orchestrator 角色
- 先維持既有 public symbol 導出，避免一次性破壞

---

## Out of scope
- 不改翻譯策略（batch/retry/key rotation）
- 不改 UI

---

## 刪除/移除/替換說明
- **刪除/替換項目**：`lm_translator_main.py` 內搬移後的 helper 定義
- **為什麼改**：單檔責任過重，維護成本高
- **現況 caller**：`lm_translator.py` 與其他 core 模組
- **替代路徑**：改為 import 新拆分模組
- **風險**：循環引用、漏搬符號、路徑回寫行為偏差
- **驗證依據**：caller 檢索 + import smoke + 全量 pytest

---

## Validation checklist
- [ ] `rg -n "from translation_tool\.core\.lm_translator_main import" translation_tool`
- [ ] `uv run python -c "from translation_tool.core.lm_translator_main import translate_batch_smart; print('ok')"`
- [ ] `uv run python -c "from translation_tool.core.lm_translator import translate_directory_generator; print('ok')"`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr35 -o cache_dir=.pytest-cache\pr35`

---

## Rejected approaches
1) **方案**：一次性重寫 `lm_translator_main.py`。  
   **放棄原因**：風險高、很難定位回歸來源。  
2) **方案**：只改註解，不拆模組。  
   **放棄原因**：無法改善耦合。  
3) **最終採用**：Phase 1 先搬函式與切邊界，行為保持。

---

## Next
PR36：`lang_merger.py` 第一階段切分。
