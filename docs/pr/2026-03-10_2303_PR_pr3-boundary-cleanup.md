# PR Title
chore: PR3 boundary cleanup

# PR Description

## Summary
這個 PR 聚焦在邊界整理，目標是把 `config_manager.py` 的 import side effect 拿掉、把 `main.py` 的啟動責任收斂成單一入口，並在 `app/services.py` 先補一層 config 存取包裝，為後續分層做準備。

---

## Phase 1 完成清單

1. `translation_tool/utils/config_manager.py`
   - 移除 module import 時直接執行的：
     - `config = load_config()`
     - `setup_logging(config)`
   - 改成輸出 `LazyConfigProxy` 作為 `config`，保留舊介面相容性
   - 避免 import module 就觸發 config I/O 與 logging 初始化 side effect

2. `main.py`
   - 新增 `bootstrap_runtime()`
   - 將 `load_config()` / `setup_logging()` 收斂到 `if __name__ == "__main__":` 的啟動入口內
   - `import main` 時不再主動執行 runtime 初始化

3. `app/services.py`
   - 移除 top-level `load_config` / `save_config` 直接 import
   - 新增 `_load_app_config()` / `_save_app_config()` 作為 config 存取包裝層
   - `load_config_json()` / `save_config_json()` / `update_logger_config()` 改走包裝層，先做分層準備

4. 備份
   - 已建立備份：`backups/pr3_20260310_225827/`

---

## Phase 2 Validation checklist

### 1) `uv run pytest`

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.0.2, pluggy-1.6.0
rootdir: C:\Users\admin\Desktop\Minecraft_translator_flet
configfile: pyproject.toml
plugins: anyio-4.11.0
collected 23 items / 3 errors

=================================== ERRORS ====================================
_______________ ERROR collecting tests/test_cache_controller.py _______________
ImportError while importing test module 'C:\Users\admin\Desktop\Minecraft_translator_flet\tests\test_cache_controller.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
..\..\AppData\Roaming\uv\python\cpython-3.12.10-windows-x86_64-none\Lib\importlib\__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests\test_cache_controller.py:1: in <module>
    from app.views.cache_controller import CacheController
E   ModuleNotFoundError: No module named 'app.views.cache_controller'
_______________ ERROR collecting tests/test_cache_presenter.py ________________
ImportError while importing test module 'C:\Users\admin\Desktop\Minecraft_translator_flet\tests\test_cache_presenter.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
..\..\AppData\Roaming\uv\python\cpython-3.12.10-windows-x86_64-none\Lib\importlib\__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests\test_cache_presenter.py:1: in <module>
    from app.views.cache_presenter import CachePresenter
E   ModuleNotFoundError: No module named 'app.views.cache_presenter'
____________ ERROR collecting tests/test_cache_view_state_gate.py _____________
ImportError while importing test module 'C:\Users\admin\Desktop\Minecraft_translator_flet\tests\test_cache_view_state_gate.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
..\..\AppData\Roaming\uv\python\cpython-3.12.10-windows-x86_64-none\Lib\importlib\__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests\test_cache_view_state_gate.py:1: in <module>
    from app.views.cache_controller import CacheController
E   ModuleNotFoundError: No module named 'app.views.cache_controller'
=========================== short test summary info ===========================
ERROR tests/test_cache_controller.py
ERROR tests/test_cache_presenter.py
ERROR tests/test_cache_view_state_gate.py
!!!!!!!!!!!!!!!!!!! Interrupted: 3 errors during collection !!!!!!!!!!!!!!!!!!!
============================== 3 errors in 2.04s ==============================
```

判讀：符合 PR2 baseline，仍是既有 3 個 collection error，沒有新增。

---

### 2) `grep -rn "import config_manager\|from config_manager" app/`

```text
(no output)
```

判讀：`app/` 底下沒有 bare `config_manager` import；本次 `services.py` 已改成 lazy import 包裝。

---

### 3) `uv run python -c "from app import services"`

```text
(no output)
```

判讀：import 成功，無新錯誤。

---

### 4) `uv run python -c "import main"`

```text
(no output)
```

判讀：`main.py` 在責任收斂後仍可正常 import，無例外。

---

### 5) `git diff --stat`

```text
warning: in the working copy of 'main.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'translation_tool/utils/config_manager.py', LF will be replaced by CRLF the next time Git touches it
 app/services.py                          |  47 ++++------
 app/task_session.py                      |   3 -
 app/views/extractor_view.py              |   2 +-
 main.py                                  | 152 ++++++++++---------------------
 translation_tool/utils/config_manager.py |  40 +++++++-
 5 files changed, 102 insertions(+), 142 deletions(-)
```

判讀：這一條 **不符合 checklist 預期**，因為工作樹裡還包含 PR2 尚未 commit 的變更：
- `app/task_session.py`
- `app/views/extractor_view.py`

補充說明：若只看 PR3 本次 scope，實際修改檔案仍是：
- `translation_tool/utils/config_manager.py`
- `main.py`
- `app/services.py`

---

## Changed files in this PR scope

- `translation_tool/utils/config_manager.py`
- `main.py`
- `app/services.py`

---

## Notes

- 這次一度踩到 `config_manager.py` 編碼汙染，已用備份檔完整回寫後再套用 lazy proxy，最後已通過 `py_compile` 與 checklist 驗證。
- `git diff --stat` 未能只剩 3 檔，不是因為 PR3 擴散，而是因為 PR2 變更目前仍在 working tree；若要讓這條完全符合，需先把 PR2 落成 commit 或另做切分。
