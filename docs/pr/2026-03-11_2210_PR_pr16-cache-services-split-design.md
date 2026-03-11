# PR16（設計）— 抽離 cache UI services 到 `services_impl/cache/`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 前置：
> - PR13：已建立 `app/services_impl/` 骨架
> - PR14：已抽離 logging（`services_impl/logging_service.py`）
> - PR15：已抽離 config/rules IO（`services_impl/config_service.py`）
> - PR12：search orchestration 已穩定落地於 `translation_tool/utils/cache_search.py`
> 本輪狀態：已實作 / 已驗證（PR16 落地：抽離 cache UI services wrappers；不改 cache_manager/search orchestration 行為）。

---

## 一句話總結

`app/services.py` 內的 cache UI services 已是一組相對獨立的 façade（給 `cache_view.py` 用），建議在 PR16 把它們抽離到 `app/services_impl/cache/cache_services.py`（先集中單檔降低搬移風險），並由 `app/services.py` re-export 同名函式維持 UI 相容；同時明確標註它們依賴 PR12 的 search orchestration API（`cache_manager.search_cache/rebuild_search_index*`）。

---

## 1) Phase 0 盤點：app/services.py 內的 cache UI services

### 1.1 服務函式清單（目前都在 app/services.py）
- `cache_get_overview_service() -> Dict[str, Any]`
- `cache_reload_service() -> Dict[str, Any]`
- `cache_reload_type_service(cache_type: str) -> Dict[str, Any]`
- `cache_save_all_service(write_new_shard: bool = True, only_types: list[str] | None = None) -> Dict[str, Any]`
- `cache_search_service(cache_type: str, query: str, mode: str = "key", limit: int = 5000) -> Dict[str, Any]`
- `cache_get_entry_service(cache_type: str, key: str) -> Dict[str, Any] | None`
- `cache_update_dst_service(cache_type: str, key: str, new_dst: str) -> bool`
- `cache_rotate_service(cache_type: str) -> bool`
- `cache_rebuild_index_service() -> Dict[str, Any]`

### 1.2 cache_view.py 直接依賴面（摘要）
`app/views/cache_view.py` 直接呼叫（至少）：
- overview / reload / save_all / rebuild_index
- get_entry / update_dst / rotate
- search

結論：PR16 需要保證 `from app.services import cache_*_service` 完全相容。

### 1.3 與 PR12 search orchestration 的依賴面（SSOT）
cache UI services 目前主要依賴 `translation_tool.utils.cache_manager`：
- overview：`cache_manager.get_cache_overview()`
- reload：`cache_manager.reload_translation_cache()`
- reload_type：`cache_manager.reload_translation_cache_type()`
- save：`cache_manager.save_translation_cache()`
- rotate：`cache_manager.force_rotate_shard()`
- search（PR12 後）：
  - `cache_manager.search_cache(...)`（內部委派給 PR12 的 search orchestration）
  - 失敗時 fallback 線性掃描：`cache_manager.get_cache_dict_ref(...)`
- rebuild index（PR12 後）：
  - `cache_manager.rebuild_search_index()`
  - / `rebuild_search_index_for_type()`（在 reload_type 中）

> PR16 的設計假設：PR12 的 search orchestration 已穩定，PR16 不改 search 行為，只搬 service wrapper。

---

## 2) PR16 設計

### 2.1 目標
- 抽離 cache UI services 到 `app/services_impl/cache/` 下的實作模組
- `app/services.py` 保持 façade/re-export，讓 UI caller 不用改任何 import

### 2.2 Scope（In scope）
- 新增 `app/services_impl/cache/cache_services.py`，把第 1.1 的函式 **原樣搬入**
- 修改 `app/services.py`：
  - 改為 `from app.services_impl.cache.cache_services import ...` re-export 同名函式
- 保持行為不變：
  - 不改回傳格式（items/truncated/limit 等）
  - 不改 search fallback 邏輯
  - 不改 cache_manager 的呼叫方式

### 2.3 Out-of-scope（本 PR 不做）
- 不調整 `cache_view.py` UI 行為或事件流程
- 不調整 PR12 的 search orchestration / cache_manager 內部實作
- 不新增新的 cache service API（例如 history service；目前 cache_view 的 history 主要是 UI 內部處理）

### 2.4 模組放置策略：先集中單檔（推薦）
- 建議 PR16 先集中在：`app/services_impl/cache/cache_services.py`
- 理由：
  - 這組函式高度耦合 `cache_manager` 與回傳格式；先集中可降低搬移時的 circular import 與散落風險
  - 後續若要再細分（例如 search vs write vs overview），可在 PR17+ 再拆

### 2.5 façade / re-export 相容策略
- PR16 後仍保證：
  - `from app.services import cache_get_overview_service, ...` 不需改
  - `import app.services as s; s.cache_search_service(...)` 也可用

---

## 3) 驗證方式（PR16 預計必做）

### 3.1 import / smoke
- `uv run python -c "import app.services as s; required=[...]; print('missing', [x for x in required if not hasattr(s,x)])"`
- `uv run python -c "from app.services_impl.cache import cache_services"`

### 3.2 pytest（最低集合）
- `uv run pytest -q tests/test_cache_view_features.py`
- `uv run pytest -q tests/test_cache_search_orchestration.py`
- 建議再加：`uv run pytest -q tests/test_main_imports.py tests/test_ui_refactor_guard.py`

---

## 4) 風險與 rollback

### 4.1 主要風險
- import 順序 / circular import：cache_services 不應 import views。
- 回傳格式不一致：cache_view 依賴 items/truncated/limit 與 key/preview/rank/score 欄位。
- search 行為誤改：PR16 只搬 wrapper，不能動 PR12 的 orchestration 介面。

### 4.2 rollback
- 因對外 API 不變：回退 PR16 即可回到原本 services.py 內實作。

---

## 5) 本輪新增/修改檔案（PR16 實作預期）
- 新增：`app/services_impl/cache/__init__.py`
- 新增：`app/services_impl/cache/cache_services.py`
- 修改：`app/services.py`（改為 re-export）
- 更新：`docs/pr/2026-03-11_2210_PR_pr16-cache-services-split-design.md`

---

## 6) 本輪驗證（已實作 / 已驗證）
- import/smoke：
  - `uv run python -c "import app.services as s; from app.services_impl.cache import cache_services as cs; required=[\"cache_get_overview_service\",\"cache_reload_service\",\"cache_reload_type_service\",\"cache_save_all_service\",\"cache_search_service\",\"cache_get_entry_service\",\"cache_update_dst_service\",\"cache_rotate_service\",\"cache_rebuild_index_service\"]; print('missing', [x for x in required if not hasattr(s,x)]); print('same', s.cache_search_service is cs.cache_search_service)"`
  - `uv run python -c "from app.services_impl.cache import cache_services"`

- pytest（要求集合）：
  - `uv run pytest -q tests/test_cache_view_features.py tests/test_cache_search_orchestration.py tests/test_main_imports.py tests/test_ui_refactor_guard.py`

- 保留盤點指令（仍可用於後續回歸定位）：
  - `rg -n "cache_(get_overview|reload|save_all|search|get_entry|update_dst|rotate|rebuild_index)_service\(" -S app/views/cache_view.py`

---

## 7) 需要家豪先決策的點
1. PR16 要先集中單檔 `cache_services.py`（推薦）還是一開始就拆多檔？
   - 建議：先集中單檔（降低搬移風險、保持 review 容易），後續再拆。
