# PR Title
docs: add maintainability comments to PR1-PR3 touched code

# PR Description

## Summary
這個 PR 不改功能，只補人工維護友善的註解，讓後續閱讀 PR1～PR3 觸及過的程式碼時，不需要靠上下文猜：

- 這段的責任邊界在哪裡
- 為什麼這裡要 lazy import / lazy config
- UI 與背景任務狀態如何分工
- `main.py` 的啟動責任與 UI 組裝為什麼要拆開

本次只補註解 / docstring / 說明性空白整理，不混入邏輯修改。

---

## Phase 1 完成清單

1. `app/services.py`
   - 補上 `UI_LOG_HANDLER` 的角色說明
   - 補上 `_load_app_config()` / `_save_app_config()` 為何保留包裝層
   - 補上 `update_logger_config()` 為何每次任務開始時重讀設定

2. `app/task_session.py`
   - 補上 `TaskSession` 作為 UI / worker 單一共享狀態面的說明
   - 補上 `snapshot()` 為何用快照避免 race condition

3. `app/views/extractor_view.py`
   - 補上 `TaskSession` 在提取頁中的用途說明
   - 補上預覽流程為何走 generator + poller，而不是走 service wrapper
   - 補上 `preview_state` 為何採前後景分工的說明

4. `main.py`
   - 補上 `bootstrap_runtime()` 為何只能在 script entry 呼叫
   - 補上 `main(page)` 只負責 UI 組裝、不做 runtime 初始化
   - 補上 `wrap_view()`、`nav_destinations` / `view_window_sizes` 的維護關係
   - 補上啟動時索引重建為何放背景執行緒

5. `translation_tool/utils/config_manager.py`
   - 補上 `DEFAULT_CONFIG` 的角色說明
   - 補上 `setup_logging()` 為何不能在 import 階段偷跑
   - 補上 `LazyConfigProxy` 的存在理由與相容性目的
   - 補上對外維持 `config` 名稱的原因

6. 備份
   - 已建立備份：`backups/pr4_20260310_232902/`

---

## Phase 2 Validation checklist

### 1) `uv run pytest`

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.0.2, pluggy-1.6.0
rootdir: C:\Users\admin\Desktop\Minecraft_translator_flet
configfile: pyproject.toml
plugins: anyio-4.11.0
collected 20 items / 3 errors

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
============================== 3 errors in 2.67s ==============================
```

判讀：仍是既有 3 個 collection error，未新增新的 import / runtime 失敗。

---

### 2) `uv run python -c "from app import services"`

```text
(no output)
```

判讀：import 成功。

---

### 3) `uv run python -c "import main"`

```text
(no output)
```

判讀：import 成功。

---

### 4) `git diff --stat`

```text
warning: in the working copy of 'main.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'translation_tool/utils/config_manager.py', LF will be replaced by CRLF the next time Git touches it
 app/services.py                          | 14 +++++++++++---
 app/task_session.py                      | 14 ++++++++++++--
 app/views/extractor_view.py              | 13 ++++++++++---
 main.py                                  | 12 ++++++++++++
 translation_tool/utils/config_manager.py | 14 ++++++++++++--
 5 files changed, 57 insertions(+), 10 deletions(-)
```

判讀：只出現在 PR4 scope 的 5 個檔案。

---

### 5) `git diff`

```text
warning: in the working copy of 'main.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'translation_tool/utils/config_manager.py', LF will be replaced by CRLF the next time Git touches it
diff --git a/app/services.py b/app/services.py
index 3e2a2e4..7dd4a1a 100644
--- a/app/services.py
+++ b/app/services.py
@@ -86,10 +86,10 @@ from translation_tool.utils import cache_manager
 
 from translation_tool.utils.log_unit import log_warning, log_error, log_debug, log_info
 
-# 1. 確保 Handler 設定正確
+# UI_LOG_HANDLER 負責把核心層 logger 轉送到畫面上的 TaskSession。
+# 它只處理「怎麼把 log 丟進 UI」；至於 log 量太大時如何節流，交給下面的 GLOBAL_LOG_LIMITER。
 UI_LOG_HANDLER = UISessionLogHandler()
 UI_LOG_HANDLER.setLevel(logging.INFO)
-# UI 顯示建議簡潔，只留 message
 UI_LOG_HANDLER.setFormatter(logging.Formatter("%(message)s"))
 
 
@@ -99,12 +99,16 @@ REPLACE_RULES_PATH = os.path.join(os.getcwd(), "replace_rules.json")
 
 
 def _load_app_config():
+    # 保留這層包裝的目的，是把 service 層對 config_manager 的依賴集中在單一入口。
+    # 之後如果要改成別的設定來源或做快取/注入，優先改這裡，不要讓 service 呼叫點四散。
     from translation_tool.utils.config_manager import load_config
 
     return load_config(CONFIG_PATH)
 
 
 def _save_app_config(config):
+    # 與 _load_app_config() 成對：service 層只知道「存設定」，
+    # 不直接綁死 config_manager 的實作細節。
     from translation_tool.utils.config_manager import save_config
 
     return save_config(config, CONFIG_PATH)
@@ -128,7 +132,11 @@ def save_config_json(config):
 # ------------------------------------------------------
 
 def update_logger_config():
-    """重新讀取 config 並套用最新的 Log 等級"""
+    """重新讀取 config 並套用最新的 Log 等級。"""
+
+    # 這裡故意在任務開始前重讀一次設定，而不是只在程式啟動時初始化：
+    # 使用者可以在 UI 裡修改 log level / format，下一輪任務就應該吃到新設定，
+    # 不需要重開整個 app。
     _config = _load_app_config()
     _log_cfg = _config.get("logging", {})
     
diff --git a/app/task_session.py b/app/task_session.py
index 09caf25..da63925 100644
--- a/app/task_session.py
+++ b/app/task_session.py
@@ -4,7 +4,14 @@ from collections import deque
 
 class TaskSession:
     """
-    單一長任務的 UI 狀態容器（Single Source of Truth）
+    單一長任務的 UI 狀態容器（Single Source of Truth）。
+
+    這個物件是 UI 執行緒與背景 worker 之間共享的最小狀態面：
+    - worker 只負責寫入 progress / logs / status
+    - UI 只透過 snapshot() 讀取快照後再決定如何渲染
+
+    這樣做的目的不是把所有事情都塞進 session，
+    而是把跨執行緒共享的狀態收斂到單一地方，降低 race condition 與散落旗標的維護成本。
     """
     def __init__(self, max_logs: int = 300):
         self.progress: float = 0.0
@@ -48,7 +55,10 @@ class TaskSession:
 
     def snapshot(self):
         """
-        UI 用的快照（避免 race condition）
+        回傳 UI 用的不可變快照。
+
+        UI 不直接拿著內部 deque / 欄位引用來讀，
+        而是每次取一份快照，避免畫面更新時撞上背景執行緒正在寫入。
         """
         with self._lock:
             return {
diff --git a/app/views/extractor_view.py b/app/views/extractor_view.py
index ad9c9c4..e7ea33d 100644
--- a/app/views/extractor_view.py
+++ b/app/views/extractor_view.py
@@ -17,7 +17,9 @@ class ExtractorView(ft.Column):
         self.page = page
         self.file_picker = file_picker
 
-        # ===== TaskSession =====
+        # ExtractorView 的長任務狀態全部收斂到 TaskSession。
+        # 背景執行緒只寫 session，UI 端靠 poller 讀快照更新畫面，
+        # 這樣提取流程與畫面狀態不會互相纏在一起。
         self.session = TaskSession(max_logs=2000)
         self._ui_poller_stop = threading.Event()
         self._last_rendered_log_count = 0
@@ -471,7 +473,11 @@ class ExtractorView(ft.Column):
     # 預覽功能
     # ==================================================
     def show_preview(self, mode: str):
-        """顯示提取預覽對話框（背景執行 + 進度條更新）"""
+        """顯示提取預覽對話框（背景執行 + 進度條更新）。"""
+
+        # 預覽故意不走 app.services 的 wrapper，因為這裡需要的是「逐步回報進度」：
+        # UI 直接吃 generator update，比包成一次回傳的 service 更容易維持預覽進度條與結果對話框。
+        # 換句話說，提取流程偏 service façade；預覽流程偏 UI orchestration。
         mods_dir = (self.mods_dir_textfield.value or "").strip()
 
         if not mods_dir:
@@ -490,7 +496,8 @@ class ExtractorView(ft.Column):
         # 鎖定按鈕
         self.set_controls_disabled(True)
 
-        # 共享狀態
+        # 預覽流程是「背景掃描 + 前景輪詢」：
+        # do_preview() 只負責推進狀態，poll() 專心把狀態轉成 UI，避免背景執行緒直接碰太多 Flet 控制項。
         preview_state = {
             'progress': 0.0,
             'current': 0,
diff --git a/main.py b/main.py
index a81ff74..5a031f9 100644
--- a/main.py
+++ b/main.py
@@ -17,6 +17,11 @@ logger = logging.getLogger("main_app")
 
 
 def bootstrap_runtime():
+    """初始化 runtime，但只應在 script entry 被呼叫一次。"""
+
+    # main.py 可以被測試或其他模組 import；
+    # runtime 初始化（讀 config / 設定 logging）不能在 import 階段偷跑，
+    # 否則 `import main` 就會帶出 side effect。
     from translation_tool.utils.config_manager import load_config, setup_logging
 
     config = load_config()
@@ -29,6 +34,8 @@ def bootstrap_runtime():
 
 
 def main(page: ft.Page):
+    # 這個函式只負責組裝 Flet UI 與頁面切換邏輯；
+    # runtime 初始化、logging 設定等啟動責任都留在 bootstrap_runtime()。
     page.title = "Minecraft 模組包繁體化工具"
     page.window_width = 1200
     page.window_height = 850
@@ -48,6 +55,8 @@ def main(page: ft.Page):
     file_picker = ft.FilePicker()
     page.overlay.append(file_picker)
 
+    # 所有頁面都先經過 wrap_view()，把一致的卡片外框與邊距集中在 UI 共用層，
+    # 避免 main.py 再變回樣式雜物間。
     config_view = wrap_view(ConfigView(page))
     rules_view = wrap_view(RulesView(page))
     cache_view = wrap_view(CacheView(page))
@@ -56,6 +65,8 @@ def main(page: ft.Page):
     lm_view = wrap_view(LMView(page, file_picker))
     merge_view = wrap_view(MergeView(page, file_picker))
 
+    # nav_destinations 與 view_window_sizes 共享同一組 selected_index。
+    # 後面若有新增/刪除頁面，兩邊要一起維護，不然切頁時視窗尺寸會對錯頁。
     nav_destinations = [
         (ft.Icons.SETTINGS, "設定", config_view),
         (ft.Icons.RULE, "規則", rules_view),
@@ -149,6 +160,7 @@ def main(page: ft.Page):
     page.update()
 
     def _rebuild_index_on_startup():
+        # 索引重建放背景執行，避免主畫面啟動時被 I/O 卡住。
         try:
             cache_rebuild_index_service()
             logger.info("啟動時全域搜尋索引重建完成")
diff --git a/translation_tool/utils/config_manager.py b/translation_tool/utils/config_manager.py
index a6df1c2..61139a6 100644
--- a/translation_tool/utils/config_manager.py
+++ b/translation_tool/utils/config_manager.py
@@ -6,7 +6,8 @@ import logging
 from datetime import datetime
 import copy
 
-# 提供一個最完整的預設設定，確保即使 config.json 不存在，程式也能正常運作
+# DEFAULT_CONFIG 是「缺檔或缺欄位時的保底值」，不是要取代使用者設定；
+# load_config() 會用它做深度合併，讓新欄位可以向後相容地補進舊 config.json。
 DEFAULT_CONFIG = {
   "logging": {
     "log_level": "INFO",
@@ -199,7 +200,10 @@ def save_config(config, config_path='config.json'):
         return False
 
 def setup_logging(config):
-    """根據設定檔配置 logging"""
+    """根據設定檔配置 logging。"""
+    # 這個函式只做 logging 初始化本身；
+    # 何時呼叫它，交給 main.bootstrap_runtime() 等 entry point 決定，
+    # 避免 import module 時就把全域 logger 狀態改掉。
     # 🔥 關鍵修正：將 flet 模組的日誌級別提高 🔥
     flet_logger = logging.getLogger("flet")
     flet_logger.setLevel(logging.WARNING) # 或 logging.ERROR
@@ -281,6 +285,10 @@ def deep_merge(default: dict, override: dict) -> dict:
 class LazyConfigProxy:
     """延遲讀取 config，避免 module import 時就觸發 I/O 與 logging 初始化。"""
 
+    # 這個 proxy 的目的是「保留舊介面相容性」：
+    # 舊模組仍可用 `from config_manager import config`，
+    # 但實際讀檔時機延後到真正取值的那一刻，而不是 import 當下。
+
     def _current(self) -> dict:
         return load_config()
 
@@ -315,4 +323,6 @@ class LazyConfigProxy:
         return repr(self._current())
 
 
+# 對外仍維持 `config` 這個名稱，讓既有呼叫點不用一次大改；
+# 真正的目標是先移除 import-time side effect，再逐步收斂舊依賴。
 config = LazyConfigProxy()
```

判讀：只有註解 / docstring 類變更，沒看到邏輯修改。

我現在可以接著把這份整理成正式 PR 檔：`docs/pr/2026-03-10_2351_PR_pr4-maintainability-comments.md`，再傳給你。```text
