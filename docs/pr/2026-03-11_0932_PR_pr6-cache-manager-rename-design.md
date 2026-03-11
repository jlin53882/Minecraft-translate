# PR Title
refactor: rename cache_manger package to cache_manager with `app.views.__init__` compatibility bridge

# PR Description

## Summary
PR6 的目標是正式處理 `cache_manger` 這個 naming debt，把 canonical package 名稱收斂成正確的 `cache_manager`。

這顆 PR **只改 code + test**，不動 README / changelog / 舊 PR 文件 / `.agentlens` 分析檔。

同時，PR6 必須保留舊 import 相容性，但相容層**統一放在 `app/views/__init__.py`**，不要在 `cache_manger/` 內再留第二層 bridge，避免 alias 疊 alias 讓後續維護者看不懂。

---

## 已確認決策

1. **compatibility bridge 放哪裡**
   - 放在 `app/views/__init__.py`
   - 不在 `cache_manger/` 內再留一層 bridge

2. **PR6 是否改 docs**
   - 不改
   - PR6 只改 code + test
   - README / docs / 舊 PR 文件 / `.agentlens` 另開 docs follow-up

3. **guard test 是否同 PR 一起改**
   - 要
   - 若不一起改，rename 完 test 仍會卡在舊名字，等於沒做完

4. **流程要求**
   - PR6 屬於 package / module / import 結構變更
   - 已先完成 Phase 0 盤點
   - 符合新的 ITERATION_SOP 規則：Phase 1 前先掃清 guard test、import 依賴點、硬編碼路徑

---

## Phase 0 盤點摘要

### Runtime / code import 目前依賴點
- `app/views/__init__.py`
  - `from .cache_manger import cache_controller as _cache_controller`
  - `from .cache_manger import cache_presenter as _cache_presenter`
  - `from .cache_manger import cache_types as _cache_types`
- `app/views/cache_view.py`
  - `from app.views.cache_manger.cache_overview_panel import build_overview_page`

### 測試層依賴
- `tests/test_ui_refactor_guard.py`
  - 直接把 `cache_manger` 名稱與資料夾結構寫死
  - 會檢查：
    - `cache_view.py` import 字串
    - `app/views/cache_manger/` 底下檔案存在性

### package 自身說明
- `app/views/cache_manger/__init__.py`
  - docstring 仍寫舊名稱

### 結論
PR6 不能只做資料夾 rename；至少要一起處理：
- package rename
- runtime import update
- guard test update
- package docstring update
- 舊 import 相容策略

---

## Scope

### In scope
1. rename：`app/views/cache_manger/` → `app/views/cache_manager/`
2. 更新 `app/views/cache_view.py` runtime import
3. 更新 `app/views/__init__.py` 的 compatibility bridge，改指向 `cache_manager`
4. 更新 `tests/test_ui_refactor_guard.py`，讓 guard test 驗證新的 canonical 名稱
5. 更新 `app/views/cache_manager/__init__.py` docstring / 說明文字

### Out of scope
- README 正名
- `docs/changelog/*` 舊文件更新
- 舊 PR 文件回寫
- `.agentlens` 分析檔同步更新
- 任何 cache 業務邏輯重寫
- `translation_tool/` 任何模組調整

---

## Phase 1 完成目標

### 1. rename package
把：
- `app/views/cache_manger/`

改成：
- `app/views/cache_manager/`

預計包含：
- `__init__.py`
- `cache_controller.py`
- `cache_presenter.py`
- `cache_types.py`
- `cache_overview_panel.py`
- `cache_log_panel.py`
- `cache_shared_widgets.py`

### 2. 更新 runtime import
至少更新：
- `app/views/cache_view.py`
  - `from app.views.cache_manager.cache_overview_panel import build_overview_page`
- `app/views/__init__.py`
  - legacy alias 改指向 `cache_manager.*`

### 3. 保留 legacy import 相容性
`app/views/__init__.py` 應持續提供：
- `app.views.cache_controller`
- `app.views.cache_presenter`
- `app.views.cache_types`

但底層導向要從：
- `cache_manger.*`

改成：
- `cache_manager.*`

### 4. 更新 guard test
`tests/test_ui_refactor_guard.py` 需同步改成新 canonical 名稱：
- **function 名稱**：`test_cache_related_modules_are_grouped_under_cache_manger` → `test_cache_related_modules_are_grouped_under_cache_manager`
- import 字串斷言
- 檔案存在性斷言

### 5. 更新 package 自身說明
`app/views/cache_manager/__init__.py` 的 docstring 要一起改成正確名稱，避免 package 名與註解互相矛盾。

---

## Compatibility bridge 設計

### 原則
- **只保留一層 bridge**
- bridge 放在 `app/views/__init__.py`
- 不在 `cache_manger/` 內再留一個 package bridge

### 理由
1. PR5 已證明 package-level alias 可行
2. 雙層 alias（`app/views/__init__.py` + `cache_manger/__init__.py`）會增加理解成本
3. 這顆 PR 的目標是收斂，而不是再堆一層歷史包袱

### 預計寫法
```python
from .cache_manager import cache_controller as _cache_controller
from .cache_manager import cache_presenter as _cache_presenter
from .cache_manager import cache_types as _cache_types

sys.modules[__name__ + ".cache_controller"] = _cache_controller
sys.modules[__name__ + ".cache_presenter"] = _cache_presenter
sys.modules[__name__ + ".cache_types"] = _cache_types
```

---

## Risk assessment

### 主要風險 1：漏改 runtime import
若 `app/views/cache_view.py` 或 `app/views/__init__.py` 漏改，
`CacheView` 或 legacy import 會直接炸。

### 主要風險 2：guard test 仍卡舊名字
若只改 code 不改 `tests/test_ui_refactor_guard.py`，
full pytest 會卡在舊名稱硬編碼。

### 主要風險 3：誤把 docs 一起大範圍洗掉
這顆 PR 若把 README / changelog / 舊 PR 記錄 / `.agentlens` 一口氣全改，範圍會膨脹，降低可 review 性。

### 主要風險 4：compatibility bridge 寫錯
若 `sys.modules` alias 寫錯 module path，
雖然 canonical import 可能能用，但舊 import 會掛。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有更新 `README.md` 內的 `cache_manger/` 文字
- 沒有更新 `docs/changelog/*` 舊文件
- 沒有更新舊 PR 文件中的 `cache_manger` 提及
- 沒有更新 `.agentlens` 分析資料
- 沒有重寫 cache 業務邏輯
- 沒有碰 `translation_tool/`

---

## Next step

### PR7
在 PR6 收斂命名 debt 後，再處理 cache metadata：
- `translation_tool/utils/cache_manager.py`
  - `mod`
  - `path`

PR7 Phase 1 前要先確認：
- rebuild index / cache 的實際指令是什麼
- 驗證時要貼哪種 search result 作為證據

---

## Validation checklist

- [ ] `uv run pytest tests/test_cache_controller.py tests/test_cache_presenter.py tests/test_cache_view_state_gate.py` — 舊 import 路徑仍可 collect / 執行，不得回退成 `ModuleNotFoundError`
- [ ] `uv run pytest tests/test_ui_refactor_guard.py -k cache_related_modules_are_grouped_under_cache_manager` — guard test 必須改成新 canonical 名稱且通過
- [ ] `uv run pytest` — full pytest 必須通過，不得比 PR5 更差
- [ ] `uv run python -c "from app.views.cache_controller import CacheController; from app.views.cache_presenter import CachePresenter; from app.views.cache_types import ActionState, CacheUiState; print(CacheController.__name__, CachePresenter.__name__, ActionState.__name__, CacheUiState.__name__)"` — legacy import path 仍成立
- [ ] `uv run python -c "from app.views.cache_manager.cache_controller import CacheController; from app.views.cache_manager.cache_presenter import CachePresenter; from app.views.cache_manager.cache_types import ActionState, CacheUiState; print('ok')"` — 新 canonical package import 成功
- [ ] `uv run python -c "from app.views.cache_view import CacheView; print('ok')"` — `cache_view.py` runtime import 路徑已更新，不炸
- [ ] `git diff --stat` — 只允許變更：
  - `app/views/cache_manager/**`
  - `app/views/__init__.py`
  - `app/views/cache_view.py`
  - `tests/test_ui_refactor_guard.py`
  不得擴散到 README / docs / `.agentlens` / `translation_tool/`
- [ ] `git diff` — 必須能清楚看出是「package rename + import path update + guard test update + 單點 compatibility bridge」，不得混入其他業務邏輯改動

---

## 一句話總結
PR6 不是單純 rename 資料夾，而是要把 `cache_manger` 這顆 naming debt 收斂成新的 canonical package，同時保住 legacy import、更新 guard test，且把變更範圍壓在 code + test 內。