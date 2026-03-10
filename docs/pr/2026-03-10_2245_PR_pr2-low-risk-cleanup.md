# PR Title
chore: PR2 low-risk cleanup

# PR Description

## Summary
這個 PR 聚焦在低風險清理，不改功能流程，只移除已確認未使用的殘留項與整理 `main.py` 雜訊註解。

---

## Phase 1 完成清單

1. `app/task_session.py`
   - 移除未使用欄位 `_last_log_flush`
   - 一併移除未使用 import：`time`、`Optional`

2. `app/views/extractor_view.py`
   - 移除未使用 import：`preview_jar_extraction_service`

3. `app/services.py`
   - 移除未被實際 caller 使用的 `preview_jar_extraction_service()` wrapper
   - 預覽流程仍由 `extractor_view.py` 直接使用 `preview_extraction_generator`

4. `main.py`
   - 清掉大量雜訊註解與註解掉的 web 啟動區塊
   - 保持既有啟動流程與 NavigationRail 行為不變
   - 只做排版/註解整理，未改功能邏輯

5. 備份
   - 已建立備份：`backups/pr2_20260310_223941/`

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
============================== 3 errors in 1.28s ==============================
```

結果：與 baseline 一致，仍是既有的 3 個 collection error。

---

### 2) `grep -rn "_last_log_flush" app/`

第一次執行輸出：

```text
grep: app/__pycache__/task_session.cpython-312.pyc: binary file matches
```

後續處理：
- 已清除 `app/` 底下所有 `__pycache__/`

清除後重新驗證：

```text
(no output)
```

判讀：原始碼與快取都已確認無 `_last_log_flush`。

---

### 3) `grep -rn "preview_jar_extraction_service" app/`

第一次執行輸出：

```text
grep: app/views/__pycache__/extractor_view.cpython-312.pyc: binary file matches
```

後續處理：
- 已清除 `app/` 底下所有 `__pycache__/`

清除後重新驗證：

```text
(no output)
```

判讀：原始碼與快取都已確認無 `preview_jar_extraction_service`。

---

### 4) `python -c "from app import services"`

實際執行輸出：

```text
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "C:\Users\admin\Desktop\Minecraft_translator_flet\app\services.py", line 74, in <module>
    from translation_tool.core.ftb_translator import translate_directory_generator
  File "C:\Users\admin\Desktop\Minecraft_translator_flet\translation_tool\core\ftb_translator.py", line 10, in <module>
    import orjson
ModuleNotFoundError: No module named 'orjson'
```

判讀：裸 `python` 使用的不是專案依賴環境，所以這一條失敗原因是宿主 Python 缺少套件，非本次 PR 引入的新錯誤。

補充驗證：`uv run python -c "from app import services"`

```text
2026-03-10 22:44:48,252 - INFO - [root] - 日誌系統已成功設定。
2026-03-10 22:44:48,315 - INFO - [translation_tool.utils.cache_manager] - 🚀 高速載入完成：lang 共 76225 條翻譯 (分片數: 31)
2026-03-10 22:44:48,326 - INFO - [translation_tool.utils.cache_manager] - 🚀 高速載入完成：patchouli 共 5423 條翻譯 (分片數: 3)
2026-03-10 22:44:48,336 - INFO - [translation_tool.utils.cache_manager] - 🚀 高速載入完成：ftbquests 共 5598 條翻譯 (分片數: 18)
2026-03-10 22:44:48,356 - INFO - [translation_tool.utils.cache_manager] - 🚀 高速載入完成：kubejs 共 11880 條翻譯 (分片數: 17)
2026-03-10 22:44:48,364 - INFO - [translation_tool.utils.cache_manager] - 🚀 高速載入完成：md 共 2850 條翻譯 (分片數: 29)
2026-03-10 22:44:48,364 - INFO - [translation_tool.utils.cache_manager] - 快取統計：lang=76148, patchouli=5423, ftbquests=5598, kubejs=11880, md=2850
2026-03-10 22:44:48,536 - INFO - [translation_tool.utils.species_cache] - Wikipedia 函式庫已成功載入。
2026-03-10 22:44:48,536 - INFO - [translation_tool.utils.species_cache] - 正在初始化學名快取系統...
2026-03-10 22:44:48,537 - INFO - [translation_tool.utils.species_cache] - 快取檔案 C:\Users\admin\Desktop\Minecraft_translator_flet\學名資料庫\species_cache.tsv 不存在，將在查詢後自動建立。
2026-03-10 22:44:48,537 - INFO - [translation_tool.utils.species_cache] - Wikipedia 語言已設定為 'zh'，請求延遲為 0.5 秒。
2026-03-10 22:44:48,537 - INFO - [translation_tool.utils.species_cache] - 學名快取系統初始化完成。
```

判讀：在專案依賴環境下，`services` import 成功。

---

## Changed files

- `app/task_session.py`
- `app/views/extractor_view.py`
- `app/services.py`
- `main.py`

---

## Notes

- 這個 PR 沒有處理既有 test collection error；它們仍來自 `app.views.cache_controller` / `app.views.cache_presenter` import 路徑問題。
- 若要讓 `grep -rn ... app/` 真正回到完全 0 輸出，可在下一步清掉 `app/**/__pycache__/` 後再跑一次。
