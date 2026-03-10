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

## 刪除項目補充說明（依 2026-03-11 新標準回填）

### 刪除項目：`app/task_session.py` 的 `_last_log_flush` 欄位（含 `import time`、`from typing import Optional`）

- **為什麼改**：`_last_log_flush` 依命名看起來是要記錄某種 log flush 時間點；搭配同檔當時存在的 `import time` 與 `Optional`，原本意圖應是想把 log 節流/flush 狀態放在 `TaskSession` 內處理。這一點沒有在程式內留下完整說明，屬於 `[需確認]` 的原始設計意圖判讀。
- **為什麼能刪**：目前 `TaskSession` 的職責已經收斂成「單一長任務的 thread-safe 狀態容器」，只負責 progress / logs / status / error；真正的 log flush 節流已由 `app/services.py` 的 `LogLimiter` / `GLOBAL_LOG_LIMITER` 處理。欄位本身沒有任何讀取點，相關 import 也沒有再被使用。
- **目前誰在用 / 沒人在用**：回填驗證時，對 `app/task_session.py` 執行 `rg -n --hidden --glob '!.git/**' --glob '!**/__pycache__/**' "_last_log_flush|import time|from typing import Optional" app/task_session.py`，輸出為空，代表目前程式碼中這三者都已無使用點。舊版備份 `backups/pr2_20260310_223941/task_session.py.bak` 可看到欄位與 import 曾存在。
- **替代路徑是什麼**：不需要在 `TaskSession` 內保留替代欄位；log 節流邏輯目前由 `app/services.py` 的 `LogLimiter` / `GLOBAL_LOG_LIMITER` 承接，`TaskSession` 只保留狀態容器責任。
- **風險是什麼**：若未來其實有外部程式用反射/猴補丁直接讀 `_last_log_flush`，刪除後會出現屬性不存在；但在專案內原始碼搜尋不到任何引用，且現行 flush 節流不依賴它，因此 repo 內 runtime 風險低。
- **我是怎麼驗證的**：
  - 舊版存在性：讀取 `backups/pr2_20260310_223941/task_session.py.bak`，可見：
    ```text
    import time
    import threading
    from collections import deque
    from typing import Optional
    ...
            self._lock = threading.Lock()
            self._last_log_flush = 0.0
    ```
  - 現況無引用：
    ```text
    rg -n --hidden --glob '!.git/**' --glob '!**/__pycache__/**' "_last_log_flush|import time|from typing import Optional" app/task_session.py
    ```
    輸出：
    ```text
    (no output)
    ```

### 刪除項目：`app/views/extractor_view.py` 的 `preview_jar_extraction_service` import

- **為什麼改**：原本 `ExtractorView` 應是想沿用 `app/services.py` 的 façade 風格，把預覽功能也透過 service wrapper 呼叫，讓 view 不必直接碰 core。這可以從舊版 `extractor_view.py` 一開始直接 import `preview_jar_extraction_service` 看出來。
- **為什麼能刪**：目前預覽流程已經明確改成 `ExtractorView.show_preview()` 直接使用 `translation_tool.core.jar_processor` 的 `preview_extraction_generator` 與 `generate_preview_report`，不再透過 service wrapper；留下舊 import 只會變成未使用殘留。
- **目前誰在用 / 沒人在用**：舊版備份 `backups/pr2_20260310_223941/extractor_view.py.bak` 可看到：
  `from app.services import run_lang_extraction_service, run_book_extraction_service, preview_jar_extraction_service`
  但回填驗證時，程式碼搜尋結果只剩 `show_preview()`、`preview_extraction_generator`、`generate_preview_report`，沒有任何 `preview_jar_extraction_service` runtime 命中。
- **替代路徑是什麼**：替代路徑就在 `app/views/extractor_view.py` 的 `show_preview()`：
  - 背景執行：`preview_extraction_generator(mods_dir, mode)`
  - 完成後輸出：`generate_preview_report(result, mode, output_dir)`
- **風險是什麼**：如果有人以為預覽流程仍經過 service 層，閱讀時可能誤判呼叫鏈；但刪掉未使用 import 本身不影響 runtime，真正風險反而是保留它會讓人誤會 service 仍在被用。
- **我是怎麼驗證的**：
  - 舊版 import：讀取 `backups/pr2_20260310_223941/extractor_view.py.bak`，可見：
    ```text
    from app.services import run_lang_extraction_service, run_book_extraction_service, preview_jar_extraction_service
    ```
  - 現行預覽呼叫鏈搜尋：
    ```text
    rg -n --hidden --glob '!.git/**' --glob '!docs/**' --glob '!.agentlens/**' --glob '!**/__pycache__/**' "preview_jar_extraction_service|preview_extraction\(|preview_extraction_generator|generate_preview_report|show_preview\(" app translation_tool
    ```
    輸出節錄：
    ```text
    app\views\extractor_view.py:475:    def show_preview(self, mode: str):
    app\views\extractor_view.py:512:            from translation_tool.core.jar_processor import preview_extraction_generator
    app\views\extractor_view.py:515:                for update in preview_extraction_generator(mods_dir, mode):
    app\views\extractor_view.py:576:                        from translation_tool.core.jar_processor import generate_preview_report
    app\views\extractor_view.py:586:                        report_path = generate_preview_report(result, mode, output_dir)
    translation_tool\core\jar_processor.py:313:def preview_extraction_generator(mods_dir: str, mode: str) -> Generator[Dict[str, Any], None, None]:
    translation_tool\core\jar_processor.py:445:def generate_preview_report(result: Dict[str, Any], mode: str, output_path: str) -> str:
    ```

### 刪除項目：`app/services.py` 的 `preview_jar_extraction_service()` wrapper 整段

- **為什麼改**：這個函式原本的存在理由，應是把 JAR 預覽也包成 `app/services.py` 的一個 service façade，讓 UI 可以像 `run_lang_extraction_service()` / `run_book_extraction_service()` 一樣統一從 service 層拿功能。[需確認] 原始作者沒有再留下額外設計說明，但命名與所在位置都支持這個判讀。
- **為什麼能刪**：它不只沒人用，還是一個壞 wrapper。舊版實作是 `from translation_tool.core.jar_processor import preview_extraction` 再呼叫 `preview_extraction(mods_dir, mode)`；但目前 `jar_processor.py` 實際存在的是 `preview_extraction_generator(...)`，不存在 `preview_extraction(...)`。也就是說，這段函式即使留下，也已不是正確入口。
- **目前誰在用 / 沒人在用**：回填驗證時，原始碼搜尋 `preview_jar_extraction_service` 已無任何 runtime caller；命中只剩 PR 文件、changelog 與 `.agentlens` 分析資料。真正正在使用的預覽路徑，是 `ExtractorView.show_preview()` 直接調 `preview_extraction_generator`，並在有輸出路徑時調 `generate_preview_report`。
- **替代路徑是什麼**：不保留一個一次性 return dict 的 service wrapper；現行替代路徑就是 `app/views/extractor_view.py` 內的 generator + poller 流程：
  - `preview_extraction_generator(...)` 持續回報進度
  - `generate_preview_report(...)` 在預覽完成後輸出報告
  這條路徑更符合預覽功能需要持續更新 UI 的實際需求。
- **風險是什麼**：如果 repo 外還有未知腳本直接 import 這個 symbol，刪掉後會在 import 時失敗；但 repo 內沒有 caller，而且舊 wrapper 本身就引用不存在的 `preview_extraction(...)`，保留反而會讓未來維護者誤以為它仍是有效入口。
- **我是怎麼驗證的**：
  - 舊版函式內容：讀取 `backups/pr2_20260310_223941/services.py.bak`，可見：
    ```text
    def preview_jar_extraction_service(mods_dir: str, mode: str):
        ...
        from translation_tool.core.jar_processor import preview_extraction
        
        try:
            result = preview_extraction(mods_dir, mode)
            return result
    ```
  - 現行 repo 搜尋結果：
    ```text
    rg -n --hidden --glob '!.git/**' --glob '!docs/**' --glob '!.agentlens/**' --glob '!**/__pycache__/**' "preview_jar_extraction_service|preview_extraction\(|preview_extraction_generator|generate_preview_report|show_preview\(" app translation_tool
    ```
    輸出節錄：
    ```text
    app\views\extractor_view.py:475:    def show_preview(self, mode: str):
    app\views\extractor_view.py:512:            from translation_tool.core.jar_processor import preview_extraction_generator
    app\views\extractor_view.py:515:                for update in preview_extraction_generator(mods_dir, mode):
    app\views\extractor_view.py:576:                        from translation_tool.core.jar_processor import generate_preview_report
    app\views\extractor_view.py:586:                        report_path = generate_preview_report(result, mode, output_dir)
    translation_tool\core\jar_processor.py:313:def preview_extraction_generator(mods_dir: str, mode: str) -> Generator[Dict[str, Any], None, None]:
    translation_tool\core\jar_processor.py:445:def generate_preview_report(result: Dict[str, Any], mode: str, output_path: str) -> str:
    ```
    其中沒有任何 `preview_jar_extraction_service` runtime 命中，也沒有 `preview_extraction(...)` 函式定義。

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
