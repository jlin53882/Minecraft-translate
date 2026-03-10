# PR Title
PR5–PR7 設計稿彙整

# Purpose
這份文件把剛剛討論過的 PR5、PR6、PR7 設計稿正式整理成可討論的 `.md` 檔。

設計依據不是憑印象，而是綜合以下資料後收斂：
- `.agentlens/INDEX.md`
- `.agentlens/code-review.md`
- `.agentlens/02-app-review.md`
- `.agentlens/03-translation-tool-review.md`
- `docs/pr/2026-03-10_2215_PR_baseline-and-code-review.md`

---

# PR5 設計稿

## PR Title
fix: restore cache view compatibility imports for tests

## Summary
先把目前最卡的 baseline 問題處理掉。

目前測試仍會 import 舊路徑：
- `app.views.cache_controller`
- `app.views.cache_presenter`

但實際檔案已經拆到：
- `app/views/cache_manger/cache_controller.py`
- `app/views/cache_manger/cache_presenter.py`

PR5 不做大搬家，只做低風險相容層：
- 新增 `app/views/cache_controller.py`
- 新增 `app/views/cache_presenter.py`

內容只做 re-export / shim，讓舊 import 先活回來。

## 為什麼先做這顆
這是目前最划算的一刀：
- 風險最低
- 最快把 pytest 從 collection error 拉回來
- 不會現在就踩 `cache_manger` rename 那顆雷

## 預計變更
- `app/views/cache_controller.py`
  - `from app.views.cache_manger.cache_controller import CacheController`
- `app/views/cache_presenter.py`
  - `from app.views.cache_manger.cache_presenter import CachePresenter`
- 視情況補少量註解，明確標記這兩個檔是 compatibility shim

## Validation checklist
- [ ] `uv run pytest tests/test_cache_controller.py tests/test_cache_presenter.py tests/test_cache_view_state_gate.py` — 不得再出現 `ModuleNotFoundError: No module named 'app.views.cache_controller' / 'app.views.cache_presenter'`
- [ ] `uv run pytest` — 至少不能再是原本那 3 個 collection error；若有新失敗，必須貼完整輸出
- [ ] `uv run python -c "from app.views.cache_controller import CacheController; from app.views.cache_presenter import CachePresenter; print(CacheController.__name__, CachePresenter.__name__)"` — import 成功
- [ ] `git diff --stat` — 只允許新增這 2 個 shim 檔（若補註解則只能在這 2 檔）
- [ ] `git diff` — 內容只能是 compatibility import / 註解，不得混入 cache 邏輯修改

---

# PR6 設計稿

## PR Title
refactor: rename cache_manger package to cache_manager with compatibility bridge

## Summary
正式處理 naming debt：把 `app/views/cache_manger/` 收斂成正確命名的 `app/views/cache_manager/`。

但這顆不能硬改，必須做相容過渡，不然很容易把剛修回來的 baseline 又打爆。

## 目標
- 將 canonical package 名稱改成 `cache_manager`
- 保留過渡相容層，避免現有 import 一次全炸
- 同步更新 `cache_view.py` 與相關 import 路徑

## 建議做法

### Phase 1
- 建立新路徑：`app/views/cache_manager/`
- 搬移：
  - `cache_controller.py`
  - `cache_presenter.py`
  - `cache_log_panel.py`
  - `cache_overview_panel.py`
  - `cache_shared_widgets.py`
  - `cache_types.py`
  - `__init__.py`

### Phase 2
- 更新專案內 import，改指向 `app.views.cache_manager.*`

### Phase 3
- 舊的 `app/views/cache_manger/` 先保留 compatibility bridge
- 不要同一個 PR 就直接砍掉
- 先讓舊路徑 re-export 到新路徑
- 等確認 pytest / import / runtime 全穩，再開後續 PR 移除舊 bridge

## 為什麼不建議這顆一次刪舊路徑
因為 `.agentlens` 已經明講這裡是 naming debt，而且 tests 之前就被這塊卡死過。
硬 rename 很容易變成「名詞正了，但整包炸了」。

## Validation checklist
- [ ] `uv run pytest tests/test_cache_controller.py tests/test_cache_presenter.py tests/test_cache_view_state_gate.py` — 必須全部至少完成 import/collect，不得再出現 `cache_manger` / `cache_manager` 路徑錯誤
- [ ] `uv run pytest` — 不得比 PR5 增加新的 import / collection error
- [ ] `uv run python -c "from app.views.cache_manager.cache_controller import CacheController; from app.views.cache_manager.cache_presenter import CachePresenter; print('ok')"` — 新 canonical 路徑 import 成功
- [ ] `uv run python -c "from app.views.cache_manger.cache_controller import CacheController; from app.views.cache_manger.cache_presenter import CachePresenter; print('compat ok')"` — 舊 compatibility bridge 在過渡期仍可用
- [ ] `uv run python -c "from app.views.cache_view import CacheView; print('ok')"` — 確認 `cache_view.py` import 路徑已更新，runtime 不炸
- [ ] `git diff --stat` — 只允許 cache view 相關模組與必要 import 變更，不得擴散到 `main.py` / `services.py` / `translation_tool/`
- [ ] `git diff` — 必須能清楚看出是「rename + import path update + compatibility bridge」，不得混入功能邏輯重寫

---

# PR7 設計稿

## PR Title
feat: populate cache search metadata for mod and path fields

## Summary
`.agentlens` 在 `translation_tool/utils/cache_manager.py` 已經點出兩個明確 TODO：
- `mod`
- `path`

目前 cache search 結果裡這兩欄是空的，導致搜尋雖然有命中，但人看結果時上下文不夠，追來源很痛。

PR7 的目標就是把 cache search 結果補成「人能用」的版本。

## 目標
- 在 cache 建立 / 載入 / 搜尋結果中補齊 `mod` 與 `path`
- 不破壞既有 cache schema 讀取
- 舊 shard / 舊 cache 若缺欄位，仍可 fallback，不直接炸

## 風險點
這顆比 PR5、PR6 都更像真正功能改動，所以要保守：
- 不一次改整個 cache schema 到面目全非
- 先做 backward-compatible 欄位補充
- 如果需要 rebuild index / cache，要明確寫進 checklist

## 建議範圍
- `translation_tool/utils/cache_manager.py`
- 視需要少量碰：
  - `translation_tool/utils/cache_search.py`
  - `app/views/cache_view.py`
- 若有 schema/格式文件，補 docs

## Validation checklist
- [ ] `uv run pytest` — 不得新增新的失敗；若 baseline 原本仍有 fail，至少不能比 PR6 更差
- [ ] `uv run python -c "from translation_tool.utils import cache_manager; print('import ok')"` — import 成功
- [ ] 實跑一個最小 cache search 驗證腳本，確認搜尋結果中的 `mod` / `path` 不再是預設空字串（完整指令與輸出要貼）
- [ ] 實際截一筆 search result（或輸出 JSON 片段），確認 `mod` / `path` 不是只有欄位存在，而是真的有值
- [ ] 若 PR7 需要重建索引或 cache，必須實跑對應指令並貼完整輸出
- [ ] `git diff --stat` — 只允許 cache 相關檔案與必要 docs 變更
- [ ] `git diff` — 必須能看出是 metadata 補值 / schema 相容，不得混入 unrelated refactor

---

# 建議順序
一定照這個順序：
1. PR5：先修 baseline import compatibility
2. PR6：再處理 `cache_manger` naming debt
3. PR7：最後補 cache search metadata

不建議倒過來，倒過來只會把問題搞髒。

---

# 補充判斷

## PR5 checklist：夠
因為它本質是小型修復，重點就是：
- import 回來
- pytest 不再卡 collection
- 不要擴散改動

## PR6 checklist：夠，但一定要守住 compatibility bridge
這條不能省，不然風險會暴增。

## PR7 checklist：我已補上資料樣本驗證
避免出現「欄位加了，但內容還是空字串」這種假完成。
