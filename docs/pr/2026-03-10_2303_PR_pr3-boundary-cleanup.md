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

## 刪除項目補充說明（依 2026-03-11 新標準回填）

### 刪除項目：`translation_tool/utils/config_manager.py` module 頂層的 `config = load_config()` 與 `setup_logging(config)` 直接執行

- **為什麼改**：這兩行原本的意圖，是在任何模組 `import config_manager` 時就保證 config 與 logging 已初始化，讓其他地方可以直接拿 `config` 或沿用 logger 狀態。從備份檔尾端的 `# --- 初始化設定 ---` 區塊可以明確看出這個設計。
- **為什麼能刪**：PR3 的目標就是把 import-time side effect 拿掉。這兩行會讓單純 `import` 模組也觸發讀檔與 logging 初始化，導致依賴邊界不乾淨。刪除後不是放空，而是改成兩條替代路徑：
  1. `config = LazyConfigProxy()` 保留舊介面相容性，但把實際讀檔延後到真正取值時
  2. `main.py` 的 `bootstrap_runtime()` 明確承接啟動時的 `load_config()` / `setup_logging(config)`
- **目前誰在用 / 沒人在用**：舊版備份 `backups/pr3_20260310_225827/config_manager.py.bak` 可看到 module 尾端直接執行初始化。回填驗證時，現行 `config_manager.py` 只剩 `config = LazyConfigProxy()`；`main.py` 則明確在 `bootstrap_runtime()` 裡呼叫 `load_config()` / `setup_logging(config)`。另外 `uv run python -c "import main"` 與 `uv run python -c "from app import services"` 目前都可成功執行且無輸出，代表 import 階段已不再因 side effect 爆掉。
- **替代路徑是什麼**：
  - config 存取替代：`config = LazyConfigProxy()`
  - runtime 初始化替代：`main.bootstrap_runtime()`
  換句話說，PR3 不是刪掉功能，而是把「偷跑初始化」改成「顯式初始化 + 延遲取值」。
- **風險是什麼**：若有模組在 repo 外偷偷依賴「只要 import config_manager，logging 就自動初始化」這個隱性行為，刪除後可能出現行為差異；但 repo 內 canonical 啟動路徑已由 `main.bootstrap_runtime()` 接手，且保留 `LazyConfigProxy` 避免既有 `config` 呼叫點一次全炸。
- **我是怎麼驗證的**：
  - 舊版 side effect：讀取 `backups/pr3_20260310_225827/config_manager.py.bak`，可見：
    ```text
    # --- 初始化設定 ---
    config = load_config()
    setup_logging(config)
    ```
  - 現行承接方式：
    ```text
    rg -n --hidden --glob '!.git/**' --glob '!**/__pycache__/**' "^config = load_config\(\)$|^setup_logging\(config\)$|^config = LazyConfigProxy\(\)$" translation_tool/utils/config_manager.py
    ```
    輸出：
    ```text
    328:config = LazyConfigProxy()
    ```
    以及：
    ```text
    rg -n --hidden --glob '!.git/**' --glob '!**/__pycache__/**' "LazyConfigProxy|bootstrap_runtime\(|config = load_config\(|setup_logging\(config\)" translation_tool/utils/config_manager.py main.py
    ```
    輸出節錄：
    ```text
    translation_tool\utils\config_manager.py:285:class LazyConfigProxy:
    translation_tool\utils\config_manager.py:328:config = LazyConfigProxy()
    main.py:19:def bootstrap_runtime():
    main.py:27:    config = load_config()
    main.py:28:    setup_logging(config)
    main.py:175:        bootstrap_runtime()
    ```
  - import 驗證：
    ```text
    uv run python -c "import main"
    uv run python -c "from app import services"
    ```
    輸出：
    ```text
    (no output)
    ```

### 刪除項目：`app/services.py` top-level `from translation_tool.utils.config_manager import load_config, save_config` 直接 import

- **為什麼改**：原本 `app/services.py` 直接在模組頂層 import `load_config` / `save_config`，代表只要 import `app.services`，就會立刻把 `config_manager` 一起拉進來。PR3 的方向是把 service 層對 config_manager 的依賴收斂到單一包裝入口，降低 top-level import 耦合。
- **為什麼能刪**：刪除 top-level direct import 後，功能沒有消失，而是改由 `_load_app_config()` / `_save_app_config()` 這兩個包裝函式在需要時才做 local import。這讓 `app/services.py` 的 config 依賴變成明確且可替換，也避免 `app.services` import 階段綁死 `config_manager`。
- **目前誰在用 / 沒人在用**：舊版備份 `backups/pr3_20260310_225827/services.py.bak` 明確有：
  `from translation_tool.utils.config_manager import load_config, save_config`
  現行 `app/services.py` 已沒有這條 top-level import，改成：
  - `_load_app_config()` 內 local import `load_config`
  - `_save_app_config()` 內 local import `save_config`
  並由 `load_config_json()` / `save_config_json()` / `update_logger_config()` 走這層包裝。
- **替代路徑是什麼**：
  - 讀設定：`_load_app_config()`
  - 存設定：`_save_app_config()`
  這兩個 wrapper 就是新的 service 層入口。
- **風險是什麼**：如果未來有人又繞過 wrapper 直接在 service 內到處 import `load_config` / `save_config`，邊界會再次髒掉；但目前 repo 內已收斂到兩個單點 wrapper，風險可控。刪錯的話，最直接後果會是 `load_config_json()`、`save_config_json()` 或 `update_logger_config()` 無法正常取用設定。
- **我是怎麼驗證的**：
  - 舊版 direct import：讀取 `backups/pr3_20260310_225827/services.py.bak`，可見：
    ```text
    from translation_tool.utils.config_manager import load_config, save_config
    ```
  - 現行 import 方式：
    ```text
    rg -n --hidden --glob '!.git/**' --glob '!**/__pycache__/**' "from translation_tool\.utils\.config_manager import load_config, save_config|def _load_app_config\(|def _save_app_config\(|from translation_tool\.utils\.config_manager import load_config|from translation_tool\.utils\.config_manager import save_config" app/services.py
    ```
    輸出：
    ```text
    101:def _load_app_config():
    104:    from translation_tool.utils.config_manager import load_config
    109:def _save_app_config(config):
    112:    from translation_tool.utils.config_manager import save_config
    ```
  - top-level direct import 已消失：
    ```text
    git grep -n "from translation_tool.utils.config_manager import load_config, save_config" HEAD -- app/services.py
    ```
    輸出：
    ```text
    NO_TOP_LEVEL_DIRECT_IMPORT_IN_HEAD
    ```
  - runtime/import 驗證：
    ```text
    uv run python -c "from app import services"
    ```
    輸出：
    ```text
    (no output)
    ```

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
