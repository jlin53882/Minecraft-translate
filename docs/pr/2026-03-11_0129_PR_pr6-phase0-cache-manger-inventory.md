# PR Title
PR6 Phase 0 盤點：`cache_manger` 引用點與改名影響面

# Purpose
這份文件是 PR6 開工前的 Phase 0 盤點稿。

目的是先把 `cache_manger` 的**實際引用點、測試依賴、文件影響面**摸清楚，再決定 PR6 的改名策略，避免直接 rename package 後漏改 import、踩爆 guard test，或把 docs / 分析檔一起搞髒。

---

# 結論先講

PR6 不能只是單純把資料夾：
- `app/views/cache_manger/`
改名成：
- `app/views/cache_manager/`

因為目前至少有三層依賴同時存在：
1. **runtime import 依賴**
2. **guard test 對資料夾名稱的硬編碼依賴**
3. **README / docs / PR 記錄 / AgentLens 分析檔的文字與範例依賴**

所以 PR6 至少會是一顆：
- package rename
- import path update
- test update
- compatibility bridge 保留策略確認
- docs 更新範圍界定

的複合型整理 PR，不適合只靠「rename 一下應該沒事」處理。

---

# 1. Runtime / code import 引用點（一定要處理）

## A. `app/views/__init__.py`
這是 PR5 剛補進去的 legacy alias 層，目前直接引用 `cache_manger`：

1. `app/views/__init__.py:11`
   - `from .cache_manger import cache_controller as _cache_controller`
2. `app/views/__init__.py:12`
   - `from .cache_manger import cache_presenter as _cache_presenter`
3. `app/views/__init__.py:13`
   - `from .cache_manger import cache_types as _cache_types`

### 判讀
PR6 若直接 rename package，這裡一定要一起更新，不然：
- `app.views.cache_controller`
- `app.views.cache_presenter`
- `app.views.cache_types`
這三條 legacy import alias 會當場失效。

---

## B. `app/views/cache_view.py`
這是目前 cache UI 真正的主要消費者：

4. `app/views/cache_view.py:13`
   - `from app.views.cache_manger.cache_overview_panel import build_overview_page`

### 判讀
這條是 PR6 的實際 runtime import。
如果 package rename 後漏改它，`CacheView` 會在 import 時直接炸。

---

# 2. 測試層依賴（PR6 最大雷點）

## `tests/test_ui_refactor_guard.py`
這份測試目前不是只測功能，而是**明確把 `cache_manger` 的結構寫死**。

### 直接命中的位置
- `tests/test_ui_refactor_guard.py:56`
  - 斷言 `cache_view.py` 內必須包含：
    - `from app.views.cache_manger.cache_overview_panel import build_overview_page`
- `tests/test_ui_refactor_guard.py:60`
  - `def test_cache_related_modules_are_grouped_under_cache_manger():`
- `tests/test_ui_refactor_guard.py:63` ~ `:69`
  - 明確要求以下檔案存在於 `app/views/cache_manger/`：
    - `cache_controller.py`
    - `cache_presenter.py`
    - `cache_types.py`
    - `cache_overview_panel.py`
    - `cache_log_panel.py`
    - `cache_shared_widgets.py`

### 判讀
這代表 PR6 若要 rename package，不能只改 code import；還要同步改這份 guard test。

不然就會出現：
- code 其實能跑
- 但 test 因為名稱硬編碼而全倒

---

# 3. package 自身內容（要一起收斂）

## `app/views/cache_manger/__init__.py`
這個檔案目前 docstring 也直接寫：
- `把 cache 相關內容集中到 app/views/cache_manger/`

### 判讀
若 PR6 要做 naming debt 清理，這個 docstring 也要同步更新，否則 package 名已改，但內部說明還停在舊名字，文件會自打臉。

---

# 4. 文件 / 分析檔影響面（不一定同 PR 全改，但要有清單）

## A. README
- `README.md:106`
  - 專案結構圖內仍寫 `cache_manger/`

## B. changelog / docs
- `docs/changelog/UI_INTEGRATION_COMPLETE.md:46`
- `docs/changelog/UI_INTEGRATION_COMPLETE.md:170`

## C. 舊 PR 文件
至少包含：
- `docs/pr/2026-03-10_2215_PR_baseline-and-code-review.md`
- `docs/pr/2026-03-11_0015_PR_pr5-pr7-design-drafts.md`
- `docs/pr/2026-03-11_0053_PR_pr5-compatibility-imports.md`
- `docs/pr/2026-03-11_0058_PR_pr5-design-revision-alias-approach.md`
- `docs/pr/2026-03-11_0118_PR_pr5-compatibility-imports-final.md`

## D. AgentLens 分析檔
- `.agentlens/INDEX.md`
- `.agentlens/code-review.md`

### 判讀
這些不一定要在 PR6 第一刀全部改完，但至少要事先知道：
- 若 PR6 想保持「docs 也同步正名」，範圍會更大
- 若 PR6 想先只改 code + test，文件更新可能要列為同 PR 次要區塊，或下一顆 docs PR

---

# 5. 目前盤點出的完整清單

## 實際 code import 引用點
- `app/views/__init__.py:11`
- `app/views/__init__.py:12`
- `app/views/__init__.py:13`
- `app/views/cache_view.py:13`

## 測試層硬編碼 / 結構依賴
- `tests/test_ui_refactor_guard.py:56`
- `tests/test_ui_refactor_guard.py:60`
- `tests/test_ui_refactor_guard.py:63-69`

## package 自身說明
- `app/views/cache_manger/__init__.py`

## docs / README / analysis 提及點
- `README.md:106`
- `docs/changelog/UI_INTEGRATION_COMPLETE.md:46`
- `docs/changelog/UI_INTEGRATION_COMPLETE.md:170`
- `docs/pr/2026-03-10_2215_PR_baseline-and-code-review.md`
- `docs/pr/2026-03-11_0015_PR_pr5-pr7-design-drafts.md`
- `docs/pr/2026-03-11_0053_PR_pr5-compatibility-imports.md`
- `docs/pr/2026-03-11_0058_PR_pr5-design-revision-alias-approach.md`
- `docs/pr/2026-03-11_0118_PR_pr5-compatibility-imports-final.md`
- `.agentlens/INDEX.md`
- `.agentlens/code-review.md`

---

# 6. 對 PR6 的實際建議

## 最小必做範圍
若 PR6 要聚焦且可控，至少要包含：

1. rename：`app/views/cache_manger/` → `app/views/cache_manager/`
2. 更新 runtime import：
   - `app/views/__init__.py`
   - `app/views/cache_view.py`
3. 更新 guard test：
   - `tests/test_ui_refactor_guard.py`
4. 更新 package 自身 docstring：
   - `app/views/cache_manager/__init__.py`
5. 保留 compatibility bridge，讓舊 `cache_manger` 路徑在過渡期仍能 import

## 建議不要一口氣塞進 PR6 的項目
除非你明確想讓 PR6 變大，否則以下可考慮放到 docs follow-up：
- README 正名
- changelog 舊文更新
- 舊 PR 文件回寫
- `.agentlens` 分析檔同步更新

---

# 7. 目前最值得提前確認的決策

在 PR6 Phase 1 之前，建議先定三件事：

1. **compatibility bridge 放哪裡？**
   - 保留 `app/views/cache_manger/` 當 bridge？
   - 還是改由 `app/views/__init__.py` 統一處理舊 alias？

2. **PR6 要不要順手改 docs？**
   - 若要，範圍會變大
   - 若不要，就要在 PR 文件明確寫成 not included

3. **guard test 的新 canonical 名稱要一起改嗎？**
   - 我傾向：要
   - 不然 PR6 做完名稱還是被 test 卡回舊詞

---

# 結論
PR6 的真正風險不在「改名難不難」，而在：
- 哪些 import 會漏改
- 哪些 test 是把舊名字硬寫死
- 哪些文件要同步更新，哪些應延後

目前盤點結果已足夠開 PR6 設計稿，但不適合直接無計畫進 Phase 1。
