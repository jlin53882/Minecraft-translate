# PR37 設計稿：`cache_manager` 薄 façade（Phase 1）

## Summary
將 `translation_tool/utils/cache_manager.py` 收斂為薄 façade，責任下沉到既有 `cache_store/cache_shards/cache_search`，但對外 API 先保持相容。

---

## Phase 0 盤點（必做）
- [ ] 列出 `cache_manager` 對外 API 清單（函式名、簽名）
- [ ] 盤點 caller：`services_impl/cache/cache_services.py` 與 core 流程
- [ ] 建立 API 對照表（重構前後）
- [ ] 定義 `__all__` 名單，避免誤暴露內部符號

---

## Phase 1 設計範圍
- `cache_manager.py` 保留 façade 層
- 內部責任轉調既有模組：
  - `cache_store.py`
  - `cache_shards.py`
  - `cache_search.py`
- 補 `__all__`（或等價公開 API 白名單）

---

## Out of scope
- 不改 cache 資料格式
- 不改 UI

---

## 刪除/移除/替換說明
- **刪除/替換項目**：`cache_manager.py` 可下沉的內部實作
- **為什麼改**：降低單檔耦合與維護負擔
- **現況 caller**：cache services 與核心流程
- **替代路徑**：caller 仍走 `cache_manager`，內部轉調子模組
- **風險**：API 簽名/返回型別若改動會造成 runtime break
- **驗證依據**：API 對照 + cache 測試全過

---

## Validation checklist
- [ ] `uv run python -c "from translation_tool.utils.cache_manager import reload_translation_cache, save_translation_cache, search_cache; print('ok')"`
- [ ] `uv run pytest -q tests/test_cache_store.py tests/test_cache_shards.py tests/test_cache_search_orchestration.py --basetemp=.pytest-tmp\pr37-cache -o cache_dir=.pytest-cache\pr37-cache`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr37 -o cache_dir=.pytest-cache\pr37`
- [ ] `uv run python -c "import translation_tool.utils.cache_manager as m; print(sorted(getattr(m,'__all__',[])))"`（確認無意外暴露）

---

## Rejected approaches
1) **方案**：直接移除 `cache_manager`，caller 全改子模組。  
   **放棄原因**：破壞面過大。  
2) **方案**：維持現狀不做 façade 收斂。  
   **放棄原因**：後續核心重構會被耦合拖慢。  
3) **最終採用**：Phase 1 先做薄 façade，相容優先。

---

## Completion definition
- 對外 API 無破壞
- cache 相關測試與全量 pytest 通過
