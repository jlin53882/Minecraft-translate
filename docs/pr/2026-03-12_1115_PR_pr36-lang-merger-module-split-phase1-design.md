# PR36 設計稿：`lang_merger.py` 模組切分（Phase 1）

## Summary
`translation_tool/core/lang_merger.py` 先做 Phase 1 分層：I/O、codec、pipeline 拆開，維持既有入口行為。

---

## Phase 0 盤點（必做）
- [ ] 列出 `lang_merger.py` 主要函式搬移清單（逐符號）
- [ ] 建立固定樣本資料集（至少 1 組 zip 輸入）
- [ ] 建立 baseline 輸出（檔案數、key 數、錯誤報告）
- [ ] 確認 caller 依賴（`merge_service` 與其他流程）

> 注意：**沒有 baseline 樣本，不進 Phase 1。**

---

## Phase 1 設計範圍
### 建議切分
- `translation_tool/core/lang_merge_zip_io.py`
- `translation_tool/core/lang_codec.py`
- `translation_tool/core/lang_merge_content.py`
- `translation_tool/core/lang_merge_pipeline.py`

### 相容策略
- `lang_merger.py` 先保留主入口（或薄轉接）
- caller 暫時不感知拆分

---

## Out of scope
- 不改合併策略規則（只搬函式）
- 不改 UI

---

## 刪除/移除/替換說明
- **刪除/替換項目**：`lang_merger.py` 內搬移出去的 helper 原地定義
- **為什麼改**：單檔過胖，責任混雜
- **現況 caller**：`app/services_impl/pipelines/merge_service.py` 等
- **替代路徑**：`lang_merger.py` 轉接新模組
- **風險**：zip 路徑處理或編碼細節偏移，導致輸出變化
- **驗證依據**：固定樣本前後比對 + pytest

---

## Validation checklist
- [ ] `uv run python -c "from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip; print('ok')"`
- [ ] `uv run python -c "from app.services_impl.pipelines.merge_service import run_merge_zip_batch_service; print('ok')"`
- [ ] 固定樣本輸入前後比對：檔案數、key 數、錯誤報告一致
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr36 -o cache_dir=.pytest-cache\pr36`

---

## Rejected approaches
1) **方案**：直接刪大段舊碼再補回。  
   **放棄原因**：沒有樣本 baseline 很容易失真。  
2) **方案**：一次改策略 + 切模組。  
   **放棄原因**：混合變更不利驗證。  
3) **最終採用**：先切分不改行為，樣本比對先行。

---

## Next
PR37：`cache_manager` 薄 façade，收斂邊界。
