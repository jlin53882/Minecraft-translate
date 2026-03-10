# PR Title
PR5 設計修正稿：改採 `app.views.__init__` alias，相容舊 import 並通過 guard test

# Purpose
這份文件是 PR5 的設計修正稿。

原版 PR5 Phase 1 先新增了 3 個 root-level compatibility shim：
- `app/views/cache_controller.py`
- `app/views/cache_presenter.py`
- `app/views/cache_types.py`

這個方向雖然讓舊 import 幾乎補回來，但在 full pytest 階段被 `tests/test_ui_refactor_guard.py` 明確擋下：

- `app/views/cache_controller.py` 不應存在
- `app/views/cache_presenter.py` 不應存在
- `app/views/cache_types.py` 不應存在

因此 PR5 需要修正方案：
- **刪除三個 root-level shim 檔**
- 改為在 **`app/views/__init__.py`** 內建立 module alias
- 讓舊 import 仍然可用，同時不破壞「cache 相關模組集中在 `cache_manger/`」的結構約束

---

# 問題背景

## 已驗證事實
1. `app/views/__init__.py` 目前存在，但為空檔。
2. targeted pytest：
   - `tests/test_cache_controller.py`
   - `tests/test_cache_presenter.py`
   - `tests/test_cache_view_state_gate.py`
   在補完 3 個 shim 後可通過。
3. full pytest 失敗點改成：
   - `tests/test_ui_refactor_guard.py::test_cache_related_modules_are_grouped_under_cache_manger`
4. 失敗原因不是功能壞掉，而是測試明確要求：
   - `app/views/cache_controller.py` 不存在
   - `app/views/cache_presenter.py` 不存在
   - `app/views/cache_types.py` 不存在

這代表「root-level shim 檔」和現有架構守門測試直接衝突。

---

# 設計修正結論

PR5 改採：

1. 刪除 3 個 root-level shim 檔
2. 在 `app/views/__init__.py` 內用 `sys.modules` 註冊 alias
3. 讓這些舊路徑仍可 import：
   - `app.views.cache_controller`
   - `app.views.cache_presenter`
   - `app.views.cache_types`
4. 真正實作仍留在：
   - `app.views.cache_manger.cache_controller`
   - `app.views.cache_manger.cache_presenter`
   - `app.views.cache_manger.cache_types`

這樣可以同時滿足兩件事：
- 舊 import 不炸
- `test_ui_refactor_guard.py` 不會因為 root-level 實體檔存在而失敗

---

# 刪除項目補充說明（依刪除說明標準）

### 刪除項目：`app/views/cache_controller.py`

- **為什麼改**：原本這個 shim 檔的存在，是為了讓 `from app.views.cache_controller import CacheController` 這種舊 import 可以先活回來。
- **為什麼能刪**：因為它雖然修好了 import，卻直接違反現有 guard test 的結構規則。已知測試明確要求 `app/views/cache_controller.py` 不應存在。
- **目前誰在用 / 沒人在用**：目前舊 import 路徑是由測試在用；不是業務邏輯直接依賴這個 root-level 檔本身，而是依賴「這個 import 路徑能成立」。因此替代的不是功能，而是 import 解析方式。
- **替代路徑是什麼**：改由 `app/views/__init__.py` 內註冊 alias，將 `app.views.cache_controller` 導向 `app.views.cache_manger.cache_controller`。
- **風險是什麼**：若 alias 寫錯，舊 import 會再次失敗；若刪檔但沒補 alias，pytest 會回到 `ModuleNotFoundError`。
- **我是怎麼驗證的**：
  - targeted pytest 已證明 shim 檔可修復 import
  - full pytest 失敗訊息明確指出：
    ```text
    assert not (APP_VIEWS / "cache_controller.py").exists()
    ```
  - 這代表 root-level 檔必須移除，不能保留

### 刪除項目：`app/views/cache_presenter.py`

- **為什麼改**：原本這個 shim 檔是為了讓 `from app.views.cache_presenter import CachePresenter` 舊 import 可用。
- **為什麼能刪**：與 `cache_controller.py` 同理，guard test 明確要求 root-level `cache_presenter.py` 不應存在。
- **目前誰在用 / 沒人在用**：用的是舊 import 路徑，不是這個檔案作為實體檔本身。測試 `tests/test_cache_presenter.py` 依賴的是 import path 可解析。
- **替代路徑是什麼**：改由 `app/views/__init__.py` alias 到 `app.views.cache_manger.cache_presenter`。
- **風險是什麼**：若 alias 缺漏，`tests/test_cache_presenter.py` 會重新失敗。
- **我是怎麼驗證的**：
  - targeted pytest 通過時已證明 importer 需求存在
  - full pytest 的 guard test 又證明 root-level 檔不能存在
  - 所以要保留的是 import path，相容方式必須改，不是保留檔案

### 刪除項目：`app/views/cache_types.py`

- **為什麼改**：第二輪補的第三個 shim，是為了讓 `from app.views.cache_types import ActionState, CacheUiState` 舊 import 可用。
- **為什麼能刪**：它解了 `ModuleNotFoundError`，但同樣撞上 guard test：`app/views/cache_types.py` 不應存在。
- **目前誰在用 / 沒人在用**：`tests/test_cache_presenter.py` 直接 import `app.views.cache_types.ActionState, CacheUiState`，證明舊 import path 仍在用；但沒證據顯示一定要透過 root-level 實體檔才能提供。
- **替代路徑是什麼**：改由 `app/views/__init__.py` alias 到 `app.views.cache_manger.cache_types`。
- **風險是什麼**：如果 alias 只補 controller / presenter，忘了 `cache_types`，會重現這輪的 collection error。
- **我是怎麼驗證的**：
  - 第一次 PR5 Phase 2 失敗就是：
    ```text
    ModuleNotFoundError: No module named 'app.views.cache_types'
    ```
  - 補上 shim 後，targeted pytest 通過；但 full pytest 又因 root-level 檔存在而失敗
  - 結論是：需要保留舊 import path，但不能用實體 shim 檔來做

---

# `app/views/__init__.py` 新內容提案

目前 `app/views/__init__.py` 是空檔，可以直接承接 alias 邏輯。

建議內容如下：

```python
"""Compatibility aliases for legacy cache imports.

Keep historical import paths alive while cache-related modules remain
physically grouped under app.views.cache_manger.
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

## 為什麼用這種寫法
- `app.views.cache_controller` 這種 import 是「submodule import」，不是單純 package attribute。
- 只在 `__init__.py` 裡做 `from .cache_manger.cache_controller import CacheController` 不夠，因為 Python 仍會找 `app.views.cache_controller` 這個 module。
- 把底層 module 註冊到 `sys.modules` 才能讓舊 submodule import 路徑成立。
- 這樣可以不新增 root-level 檔案，又保留 import 相容性。

---

# 更新後的 Validation checklist

- [ ] `uv run pytest tests/test_cache_controller.py tests/test_cache_presenter.py tests/test_cache_view_state_gate.py` — 必須全部通過，不得再出現 `app.views.cache_controller` / `app.views.cache_presenter` / `app.views.cache_types` import error
- [ ] `uv run pytest tests/test_ui_refactor_guard.py -k cache_related_modules_are_grouped_under_cache_manger` — guard test 必須通過，確認 `app/views/` 根目錄不再殘留 `cache_controller.py` / `cache_presenter.py` / `cache_types.py`
- [ ] `uv run pytest` — full pytest 不得比目前結果更差；理想情況是全部通過
- [ ] `uv run python -c "from app.views.cache_controller import CacheController; from app.views.cache_presenter import CachePresenter; from app.views.cache_types import ActionState, CacheUiState; print(CacheController.__name__, CachePresenter.__name__, ActionState.__name__, CacheUiState.__name__)"` — 舊 import path 仍可成立
- [ ] `git diff --stat` — 只允許：
  - 刪除 `app/views/cache_controller.py`
  - 刪除 `app/views/cache_presenter.py`
  - 刪除 `app/views/cache_types.py`
  - 修改 `app/views/__init__.py`
  不得擴散到其他檔案
- [ ] `git diff` — 內容必須能清楚看出「刪 shim 檔 + 以 `__init__.py` alias 取代」，不得混入 cache 業務邏輯修改

---

# 風險評估

## 低風險
- `app/views/__init__.py` 目前為空，不會踩到既有內容覆蓋問題
- 真正實作模組仍維持在 `cache_manger/` 底下，不動業務邏輯

## 主要風險
- `sys.modules` alias 若拼錯 module name，會造成 import 仍失敗
- 若 alias 建立時機不對，某些 import 路徑可能仍找不到 submodule
- 若只修 controller / presenter，漏掉 `cache_types`，pytest 仍會失敗

## 為什麼值得做
因為這是目前唯一同時滿足以下兩件事的方案：
1. 舊 import path 可用
2. root-level cache 檔案不存在，guard test 可過

---

# 結論
PR5 應從「新增 root-level shim 檔」修正為「刪除 3 個 shim 檔，改在 `app/views/__init__.py` 建立 alias」。

這不是推翻原本方向，而是根據 Phase 2 真實測試結果，把相容策略從「實體 shim 檔」升級成「無新增檔案的 package alias」，讓 import 相容性與結構守門規則可以同時成立。
