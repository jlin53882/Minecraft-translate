# PR Title
fix: restore cache view compatibility imports for tests

# PR Description

## Summary
PR5 最終版改採 `app/views/__init__.py` 的 `sys.modules` alias 方案，而不是在 `app/views/` 根目錄新增 shim 檔。

這樣做同時滿足兩件事：
- 舊 import 路徑仍可用：
  - `app.views.cache_controller`
  - `app.views.cache_presenter`
  - `app.views.cache_types`
- `tests/test_ui_refactor_guard.py` 不會因 root-level cache 檔案存在而失敗

---

## Phase 1 完成清單
- [x] 做了：建立回退點 `backups/pr5c_20260311_010054/`
- [x] 做了：將 `app/views/__init__.py` 改成 `sys.modules` alias 方案
- [x] 做了：刪除 `app/views/cache_controller.py`
- [x] 做了：刪除 `app/views/cache_presenter.py`
- [x] 做了：刪除 `app/views/cache_types.py`
- [ ] 超出範圍：rename `cache_manger/` → `cache_manager/`（留給 PR6）

---

## 刪除項目補充說明

### 刪除項目：`app/views/cache_controller.py`

- **為什麼改**：原本這個 shim 檔是為了讓 `from app.views.cache_controller import CacheController` 這種舊 import 先活回來。
- **為什麼能刪**：它雖然修好了 import，卻直接違反 `tests/test_ui_refactor_guard.py` 的結構規則；guard test 明確要求 `app/views/cache_controller.py` 不應存在。
- **目前誰在用 / 沒人在用**：用的是舊 import 路徑，不是這個 root-level 檔本身。測試與可能的舊 caller 依賴的是 import path 可以解析，不要求必須有一個實體 shim 檔。
- **替代路徑是什麼**：改由 `app/views/__init__.py` 內的 `sys.modules` alias，把 `app.views.cache_controller` 導向 `app.views.cache_manger.cache_controller`。
- **風險是什麼**：若 alias 註冊錯誤或漏掉，舊 import 會再次失敗；若刪檔但不補 alias，pytest 會回到 `ModuleNotFoundError`。
- **我是怎麼驗證的**：
  - guard test 規則：
    ```text
    assert not (APP_VIEWS / "cache_controller.py").exists()
    ```
  - import 驗證：
    ```text
    uv run python -c "from app.views.cache_controller import CacheController; ..."
    ```
    輸出：
    ```text
    CacheController CachePresenter ActionState CacheUiState
    ```

### 刪除項目：`app/views/cache_presenter.py`

- **為什麼改**：原本這個 shim 檔是為了讓 `from app.views.cache_presenter import CachePresenter` 舊 import 可用。
- **為什麼能刪**：與 `cache_controller.py` 同理，guard test 明確要求 root-level `cache_presenter.py` 不應存在。
- **目前誰在用 / 沒人在用**：用的是舊 import 路徑，不是 root-level 實體檔本身；`tests/test_cache_presenter.py` 關注的是 import path 可解析。
- **替代路徑是什麼**：改由 `app/views/__init__.py` alias 到 `app.views.cache_manger.cache_presenter`。
- **風險是什麼**：若 alias 缺漏，`tests/test_cache_presenter.py` 會重新失敗。
- **我是怎麼驗證的**：
  - guard test 規則：
    ```text
    assert not (APP_VIEWS / "cache_presenter.py").exists()
    ```
  - targeted pytest：
    ```text
    tests\test_cache_presenter.py ...
    ```
    已通過。

### 刪除項目：`app/views/cache_types.py`

- **為什麼改**：這個 shim 檔是第二輪補上的相容層，用來讓 `from app.views.cache_types import ActionState, CacheUiState` 舊 import 可用。
- **為什麼能刪**：它解了 `ModuleNotFoundError`，但同樣撞上 guard test：`app/views/cache_types.py` 不應存在。
- **目前誰在用 / 沒人在用**：`tests/test_cache_presenter.py` 直接 import `app.views.cache_types.ActionState, CacheUiState`，證明舊 import path 仍在用；但沒有證據顯示一定要透過 root-level 檔案才能提供。
- **替代路徑是什麼**：改由 `app/views/__init__.py` alias 到 `app.views.cache_manger.cache_types`。
- **風險是什麼**：如果 alias 只補 controller / presenter，忘了 `cache_types`，會重現最初的 collection error。
- **我是怎麼驗證的**：
  - 先前失敗訊息：
    ```text
    ModuleNotFoundError: No module named 'app.views.cache_types'
    ```
  - 修正後 import 驗證輸出：
    ```text
    CacheController CachePresenter ActionState CacheUiState
    ```
  - guard test 也已通過。

---

## What was done

### 1. 放棄 root-level shim 檔方案
刪除：
- `app/views/cache_controller.py`
- `app/views/cache_presenter.py`
- `app/views/cache_types.py`

理由：雖然 shim 檔可以補回舊 import，但會違反 `test_ui_refactor_guard.py` 對結構分層的要求。

### 2. 改由 `app/views/__init__.py` 提供 compatibility alias
新增內容：
- `from .cache_manger import cache_controller as _cache_controller`
- `from .cache_manger import cache_presenter as _cache_presenter`
- `from .cache_manger import cache_types as _cache_types`
- `sys.modules[...] = ...` 註冊三條 legacy module alias

實際寫進去的程式碼如下：

```python
"""Compatibility aliases for legacy cache imports.

Keep historical import paths alive while cache-related modules remain
physically grouped under ``app.views.cache_manger``.
"""

from __future__ import annotations

import sys

from .cache_manger import cache_controller as _cache_controller
from .cache_manger import cache_presenter as _cache_presenter
from .cache_manger import cache_types as _cache_types

# Legacy module aliases used by tests / older callers.
sys.modules[__name__ + ".cache_controller"] = _cache_controller
sys.modules[__name__ + ".cache_presenter"] = _cache_presenter
sys.modules[__name__ + ".cache_types"] = _cache_types
```

### 3. 保留既有 cache 模組物理位置
這輪沒有搬動任何 `cache_manger/` 內的實作檔，真正業務邏輯仍留在：
- `app/views/cache_manger/cache_controller.py`
- `app/views/cache_manger/cache_presenter.py`
- `app/views/cache_manger/cache_types.py`

---

## Rejected approach
- 試過：在 `app/views/` 根目錄新增 3 個 compatibility shim 檔
  - `cache_controller.py`
  - `cache_presenter.py`
  - `cache_types.py`
- 為什麼放棄：雖然 targeted pytest 一度從 3 個 collection error 降到 1 個，後來也把 `cache_types` 補齊了，但 full pytest 被 `tests/test_ui_refactor_guard.py::test_cache_related_modules_are_grouped_under_cache_manger` 擋下來。關鍵錯誤是：
  ```text
  assert not (APP_VIEWS / "cache_controller.py").exists()
  ```
  也就是測試明確要求 root-level cache shim 檔不應存在。
- 最終改採：刪除 3 個 root-level shim，改由 `app/views/__init__.py` 用 `sys.modules` 註冊 legacy module alias，把舊 import 路徑導向 `app.views.cache_manger.*` 真實模組。

---

## Important findings

1. 原版 PR5 的 shim 方向不是全錯，但和 guard test 規則直接衝突。
2. `sys.modules` alias 比新增 root-level shim 檔更適合這顆修復，因為它能：
   - 保留 import 相容性
   - 維持 `cache_manger/` 的物理集中
3. 這顆完成後，full pytest 已從「collection error」恢復到 **27 passed**。
4. 最終進 GitHub 的程式碼 diff 只有 `app/views/__init__.py`，中途被放棄的 shim 方案則保留在 PR 文件與中間紀錄中，避免把錯誤嘗試混進正式 code history。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有 rename `cache_manger/` → `cache_manager/`
- 沒有修改 `cache_view.py` 的 import 結構
- 沒有重寫 cache 業務邏輯
- 沒有碰 `translation_tool/` 任何檔案

---

## Next step

### PR6
- 正式處理 `cache_manger` naming debt
- 採 `cache_manager` canonical 路徑 + compatibility bridge 的漸進式做法

---

## Test result

### 1) `uv run pytest tests/test_cache_controller.py tests/test_cache_presenter.py tests/test_cache_view_state_gate.py`

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.0.2, pluggy-1.6.0
rootdir: C:\Users\admin\Desktop\Minecraft_translator_flet
configfile: pyproject.toml
plugins: anyio-4.11.0
collected 7 items

tests\test_cache_controller.py ..                                        [ 28%]
tests\test_cache_presenter.py ...                                        [ 71%]
tests\test_cache_view_state_gate.py ..                                   [100%]

============================== 7 passed in 3.07s ==============================
```

### 2) `uv run pytest tests/test_ui_refactor_guard.py -k cache_related_modules_are_grouped_under_cache_manger`

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.0.2, pluggy-1.6.0
rootdir: C:\Users\admin\Desktop\Minecraft_translator_flet
configfile: pyproject.toml
plugins: anyio-4.11.0
collected 6 items / 5 deselected / 1 selected

tests\test_ui_refactor_guard.py .                                        [100%]

======================= 1 passed, 5 deselected in 0.01s =======================
```

### 3) `uv run pytest`

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.0.2, pluggy-1.6.0
rootdir: C:\Users\admin\Desktop\Minecraft_translator_flet
configfile: pyproject.toml
plugins: anyio-4.11.0
collected 27 items

tests\test_cache_controller.py ..                                        [  7%]
tests\test_cache_presenter.py ...                                        [ 18%]
tests\test_cache_view_features.py .....                                  [ 37%]
tests\test_cache_view_monkeypatch_integration.py ...                     [ 48%]
tests\test_cache_view_state_gate.py ..                                   [ 55%]
tests\test_main_imports.py .                                             [ 59%]
tests\test_ui_components.py ....                                         [ 74%]
tests\test_ui_refactor_guard.py ......                                   [ 96%]
tests\test_view_wrapper.py .                                             [100%]

============================= 27 passed in 2.31s ==============================
```

### 4) `uv run python -c "from app.views.cache_controller import CacheController; from app.views.cache_presenter import CachePresenter; from app.views.cache_types import ActionState, CacheUiState; print(CacheController.__name__, CachePresenter.__name__, ActionState.__name__, CacheUiState.__name__)"`

```text
CacheController CachePresenter ActionState CacheUiState
```

### 5) `git diff --stat -- app/views/__init__.py app/views/cache_controller.py app/views/cache_presenter.py app/views/cache_types.py`

```text
warning: in the working copy of 'app/views/__init__.py', LF will be replaced by CRLF the next time Git touches it
 app/views/__init__.py | 18 ++++++++++++++++++
 1 file changed, 18 insertions(+)
```

### 6) `git diff -- app/views/__init__.py app/views/cache_controller.py app/views/cache_presenter.py app/views/cache_types.py`

```text
warning: in the working copy of 'app/views/__init__.py', LF will be replaced by CRLF the next time Git touches it
diff --git a/app/views/__init__.py b/app/views/__init__.py
index e69de29..603ddeb 100644
--- a/app/views/__init__.py
+++ b/app/views/__init__.py
@@ -0,0 +1,18 @@
+"""Compatibility aliases for legacy cache imports.
+
+Keep historical import paths alive while cache-related modules remain
+physically grouped under ``app.views.cache_manger``.
+"""
+
+from __future__ import annotations
+
+import sys
+
+from .cache_manger import cache_controller as _cache_controller
+from .cache_manger import cache_presenter as _cache_presenter
+from .cache_manger import cache_types as _cache_types
+
+# Legacy module aliases used by tests / older callers.
+sys.modules[__name__ + ".cache_controller"] = _cache_controller
+sys.modules[__name__ + ".cache_presenter"] = _cache_presenter
+sys.modules[__name__ + ".cache_types"] = _cache_types
```
