# PR Title
fix: restore cache view compatibility imports for tests

# PR Description

## Summary
這輪先依 PR5 設計稿完成 Phase 1，新增兩個 compatibility shim：
- `app/views/cache_controller.py`
- `app/views/cache_presenter.py`

Phase 2 已依 checklist 實跑驗證，但目前 **尚未通過**。雖然原本的 `app.views.cache_controller` / `app.views.cache_presenter` collection error 已被消掉，測試進一步揭露下一個缺口：`app.views.cache_types` 仍不存在對外相容層。

---

## Phase 1 完成清單
- [x] 做了：建立回退點 `backups/pr5_20260311_005103/`
- [x] 做了：新增 `app/views/cache_controller.py`
- [x] 做了：新增 `app/views/cache_presenter.py`
- [x] 做了：兩個 shim 都只做 re-export，沒有混入 cache 邏輯
- [ ] 未做：補 `app.views.cache_types` 相容層（原因：不在本輪 Phase 1 設計稿範圍內，是 Phase 2 驗證才揭露的新缺口）

---

## What was done

### 1. 新增 legacy import compatibility shim
新增兩個檔案：
- `app/views/cache_controller.py`
- `app/views/cache_presenter.py`

內容都維持極薄，只做 re-export：
- `from app.views.cache_manger.cache_controller import CacheController`
- `from app.views.cache_manger.cache_presenter import CachePresenter`

### 2. 保留回退點
已建立：
- `backups/pr5_20260311_005103/MANIFEST.txt`

用途：記錄這輪是新增檔型的相容 shim，方便後續回退與追查。

---

## Important findings

1. **PR5 的原始目標部分達成**
   - `app.views.cache_controller` 與 `app.views.cache_presenter` 這兩個 import 缺口已補回。

2. **pytest 從 3 個 collection error 降到 1 個**
   - 新的唯一 collection error 變成：
     - `ModuleNotFoundError: No module named 'app.views.cache_types'`

3. **代表 tests 的舊 import 面不只兩個 symbol**
   - `test_cache_presenter.py` 不只依賴 presenter 本身，還直接依賴：
     - `app.views.cache_types.ActionState`
     - `app.views.cache_types.CacheUiState`

4. **`git diff --stat` / `git diff` 對 untracked 新檔沒有直接輸出**
   - 這次新增的是未追蹤新檔，`git diff --stat` 與 `git diff` 會是空輸出。
   - 實際要看這輪新增內容，需配合 `git status --short`。

---

## Not included in this PR
這個 PR **目前還沒有完成** 以下事情：
- 沒有新增 `app/views/cache_types.py` compatibility shim
- 沒有讓 `uv run pytest` 回到完全綠燈
- 沒有進 commit / push

---

## Next step

### PR5 下一步
建議直接補第三個 compatibility shim：
- `app/views/cache_types.py`
  - `from app.views.cache_manger.cache_types import ActionState, CacheUiState`

然後重跑同一份 checklist：
- targeted pytest
- full pytest
- import 驗證
- git status / diff 檢查

---

## Test result

### 1) `uv run pytest tests/test_cache_controller.py tests/test_cache_presenter.py tests/test_cache_view_state_gate.py`

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.0.2, pluggy-1.6.0
rootdir: C:\Users\admin\Desktop\Minecraft_translator_flet
configfile: pyproject.toml
plugins: anyio-4.11.0
collected 4 items / 1 error

=================================== ERRORS ====================================
_______________ ERROR collecting tests/test_cache_presenter.py ________________
ImportError while importing test module 'C:\Users\admin\Desktop\Minecraft_translator_flet\tests\test_cache_presenter.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
..\..\AppData\Roaming\uv\python\cpython-3.12.10-windows-x86_64-none\Lib\importlib\__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests\test_cache_presenter.py:2: in <module>
    from app.views.cache_types import ActionState, CacheUiState
E   ModuleNotFoundError: No module named 'app.views.cache_types'
=========================== short test summary info ===========================
ERROR tests/test_cache_presenter.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
============================== 1 error in 1.11s ===============================
```

### 2) `uv run pytest`

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.0.2, pluggy-1.6.0
rootdir: C:\Users\admin\Desktop\Minecraft_translator_flet
configfile: pyproject.toml
plugins: anyio-4.11.0
collected 24 items / 1 error

=================================== ERRORS ====================================
_______________ ERROR collecting tests/test_cache_presenter.py ________________
ImportError while importing test module 'C:\Users\admin\Desktop\Minecraft_translator_flet\tests\test_cache_presenter.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
..\..\AppData\Roaming\uv\python\cpython-3.12.10-windows-x86_64-none\Lib\importlib\__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests\test_cache_presenter.py:2: in <module>
    from app.views.cache_types import ActionState, CacheUiState
E   ModuleNotFoundError: No module named 'app.views.cache_types'
=========================== short test summary info ===========================
ERROR tests/test_cache_presenter.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
============================== 1 error in 1.17s ===============================
```

### 3) `uv run python -c "from app.views.cache_controller import CacheController; from app.views.cache_presenter import CachePresenter; print(CacheController.__name__, CachePresenter.__name__)"`

```text
CacheController CachePresenter
```

### 4) `git diff --stat`

```text
(no output)
```

### 5) `git diff -- app/views/cache_controller.py app/views/cache_presenter.py`

```text
(no output)
```

### 補充驗證：`git status --short`

```text
?? app/views/cache_controller.py
?? app/views/cache_presenter.py
```
