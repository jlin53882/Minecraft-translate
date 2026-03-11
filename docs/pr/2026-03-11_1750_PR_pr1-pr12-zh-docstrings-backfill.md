# PR1～PR12 註解 / Docstring 補強盤點與回補（繁中）

> 目的：針對 PR1～PR12 期間「被改動或新增」的程式檔，補上必要的繁體中文維護性註解 / docstring。
> 原則：只補意圖、邊界、風險、相依關係；不做逐行廢話；不改邏輯。

## Phase 0：PR1～PR12 影響的程式檔清單（僅程式檔）

以下清單來自 git diff（基準 commit `02a3d57` → HEAD `be5045f`）範圍內的 `*.py`：

- app/services.py
- app/task_session.py
- app/views/__init__.py
- app/views/cache_manager/__init__.py
- app/views/cache_manager/cache_controller.py
- app/views/cache_manager/cache_log_panel.py
- app/views/cache_manager/cache_overview_panel.py
- app/views/cache_manager/cache_presenter.py
- app/views/cache_manager/cache_shared_widgets.py
- app/views/cache_manager/cache_types.py
- app/views/cache_view.py
- app/views/extractor_view.py
- main.py
- tests/test_cache_search_orchestration.py
- tests/test_cache_shards.py
- tests/test_cache_store.py
- tests/test_ui_refactor_guard.py
- translation_tool/utils/cache_manager.py
- translation_tool/utils/cache_search.py
- translation_tool/utils/cache_shards.py
- translation_tool/utils/cache_store.py
- translation_tool/utils/config_manager.py

## 本次實際回補範圍（只動註解/docstring/必要排版）

### A) Service / entry
- app/services.py：新增模組層 docstring；補 `LogLimiter` 類別 docstring
- app/task_session.py：新增模組層 docstring
- main.py：新增模組層 docstring

### B) Cache UI
- app/views/cache_view.py：新增模組層 docstring；擴充 `CacheView` 類別 docstring
- app/views/extractor_view.py：新增模組層 docstring；新增 `ExtractorView` 類別 docstring
- app/views/__init__.py：將相容層說明改為繁中維護註解
- app/views/cache_manager/cache_controller.py：新增模組層 docstring
- app/views/cache_manager/cache_presenter.py：新增 `CachePresenter` 類別 docstring
- app/views/cache_manager/cache_types.py：新增模組層 docstring

### C) Cache core
- translation_tool/utils/cache_manager.py：新增模組層 docstring；補 `_get_cache_root()` docstring
- translation_tool/utils/cache_store.py：新增模組層 docstring
- translation_tool/utils/config_manager.py：新增模組層 docstring

## Phase0 清單逐檔狀態

### 已補強
- app/services.py
- app/task_session.py
- app/views/__init__.py
- app/views/cache_manager/cache_controller.py
- app/views/cache_manager/cache_presenter.py
- app/views/cache_manager/cache_types.py
- app/views/cache_view.py
- app/views/extractor_view.py
- main.py
- translation_tool/utils/cache_manager.py
- translation_tool/utils/cache_store.py
- translation_tool/utils/config_manager.py

### 已檢查無需補強（本輪未改）
- app/views/cache_manager/__init__.py（已有繁中模組說明）
- app/views/cache_manager/cache_log_panel.py（已有清楚的繁中 docstring）
- app/views/cache_manager/cache_overview_panel.py（已有清楚的繁中 docstring）
- app/views/cache_manager/cache_shared_widgets.py（已有清楚的繁中 docstring）
- translation_tool/utils/cache_search.py（已有完整繁中模組說明與主要函式 docstring）
- translation_tool/utils/cache_shards.py（已有繁中 docstring）
- tests/test_cache_search_orchestration.py（測試檔：已檢查，無需補強）
- tests/test_cache_shards.py（測試檔：已檢查，無需補強）
- tests/test_cache_store.py（測試檔：已檢查，無需補強）
- tests/test_ui_refactor_guard.py（測試檔：已檢查，無需補強）

### 刻意未動（需附原因）
- 無

## 刻意不動的部分（行為層面）
- 不改任何流程、函式簽名、import、變數名稱。
- `cache_manager._get_cache_root()` 仍使用 `os.getcwd()`（僅補註解揭露風險），避免打破既有啟動目錄假設。

## 驗證
- 指令：`uv run pytest -q tests/test_main_imports.py tests/test_cache_view_features.py tests/test_cache_shards.py tests/test_cache_store.py tests/test_cache_search_orchestration.py tests/test_ui_refactor_guard.py`
- 結果：22 passed
