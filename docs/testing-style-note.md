# testing-style-note.md
# Minecraft_translator_flet 測試撰寫風格筆記

> 目的：把目前這個專案在「重構 / 路徑修正 / façade 遷移 / cache orchestration」上，實際有效的測試思路整理成長期參考文件。
> 
> 這不是單顆 PR 記錄，而是未來寫測試時優先參考的工作原則。

---

## 一句話總結

這個專案的測試策略，不是先追求漂亮的大型端到端流程，而是：

1. 先保對外契約
2. 先打最容易回歸的邊界
3. 外部依賴盡量隔離
4. 用最小可驗證切面確認「改完還能不能活」

---

## 1) 測試優先順序：先測契約，不先測內部長相

在這個專案裡，很多 PR 的本質是：
- 抽離 module
- 調整 import path
- 改 façade / re-export
- 清理 legacy path resolution
- 拆 service wrapper

這類變更最容易壞的，不是演算法本身，而是：
- import 會不會炸
- caller 還能不能找到原本的符號
- UI page 還能不能正常 import
- 路徑解析會不會因 cwd / restore / backup 出問題

所以測試第一順位通常不是：
- 內部用了哪個 helper
- 某段迴圈怎麼寫
- function 內部實作步驟長得漂不漂亮

而是：
- 這個 module 還能 import 嗎？
- 這個 view 還能開嗎？
- 這個 service 對外 contract 還在嗎？
- 這個相對路徑還會不會漂掉？

---

## 2) 測試分兩層：Smoke / Contract + Behavior

### A. Smoke / Contract tests
用來抓：
- import path 錯誤
- circular import
- façade / re-export 漏接
- lazy import fallback 壞掉
- view 在 import 階段就炸掉

常見寫法：

```bash
uv run python -c "from app.services import run_merge_zip_batch_service"
uv run python -c "from app.views.translation_view import TranslationView; print('ok')"
uv run python -c "import main; print('ok')"
```

這類 smoke test 很便宜，但對 refactor 專案很有價值。

### B. Behavior tests
用來抓：
- 路徑解析真的修正到位
- cache rebuild / search 契約還在
- restore / history / save 這類行為邊界仍正確
- 特定 wrapper 的錯誤行為、欄位格式或 progress 契約沒變

Behavior test 的重點是：
- 不求全
- 只打這顆 PR 最容易回歸的行為

---

## 3) 先找「最小可驗證切面」

這個專案不適合一開始就拿大型流程做唯一驗證，因為：
- UI 頁面多
- service façade 多
- 路徑 / config / cache 狀態會互相影響
- 大型測試失敗時很難快速定位責任

比較有效的做法是先切成最小面：
- 一個 helper
- 一個 import path
- 一個 service wrapper contract
- 一個最小 state transition

例如：

### 路徑修正類 PR
不要先測「整個 app restore 流程」，而是先測：
- `load_config()` 不再跟 cwd 漂
- `_get_cache_root()` 不再跟 cwd 漂
- `load_replace_rules("replace_rules.json")` 會 resolve 到 project root

### façade / import migration 類 PR
不要先測整個 UI 點來點去，而是先測：
- `from app.views.lookup_view import LookupView` 可成功
- `from app.views.extractor_view import ExtractorView` 可成功
- `from app.views.translation_view import TranslationView` 可成功

---

## 4) 外部依賴要隔離，只測這層責任

這個專案很多東西不適合在單元測試裡真的跑到底：
- Flet UI
- Wikipedia
- 真正的使用者工作目錄
- 真正的 cache 根目錄
- 真實 search index rebuild 的所有周邊狀態

因此測試撰寫原則是：
- 能 patch 就 patch
- 能 fake 就 fake
- 能 monkeypatch 路徑就不要改真環境
- 能只測本層 contract，就不要把三層外部依賴一起拖進來

常用技巧：
- `monkeypatch.setattr(...)`
- `monkeypatch.chdir(...)`（只在真的需要測舊行為時）
- fake object（例如 `FakePage`）
- 小型 `tmp_path`

範例：
- patch `cache_manager.resolve_project_path`
- patch `config_manager.PROJECT_ROOT`
- patch `text_processor.resolve_project_path`
- patch `cache_search.rebuild_from_cache_dicts`

---

## 5) 測試名稱要直接說出「我要保住什麼」

好測試名稱的原則是：
- fail 時一眼看出在保什麼
- 未來重構時，一眼看出這顆不能隨便動
- 不要抽象，不要偷懶

### 好的例子
- `test_load_config_uses_project_root_not_cwd`
- `test_cache_root_uses_project_root_not_cwd`
- `test_replace_rules_relative_path_resolves_to_project_root`
- `test_rebuild_search_index_contract_and_tmp_cleanup`
- `test_rebuild_uses_build_then_swap_query_not_crash`

### 爛的例子
- `test_config_ok`
- `test_path_fix`
- `test_cache_work`

這種太空泛，未來 fail 時沒有診斷價值。

---

## 6) 針對這個專案，最值得優先保的區域

### A. `app.services` / `services_impl/*`
這條線是這個專案最近幾輪重構的核心。

所以測試應優先保：
- import 不爆
- caller 還找得到符號
- view import smoke 正常
- façade 遷移後 canonical import 正常

### B. `translation_view.py` 的 lazy import
這頁不是單純 import，而是：
- `try/except`
- service 壞掉時 fallback `None`

所以一旦 migration / re-export / module path 改動，這裡很容易靜默變壞。
必須優先用 view import smoke 保。

### C. `cache_manager` / `cache_view`
這條線 surface 很大：
- overview
- reload
- save
- rotate
- search
- history
- apply / restore

所以測試不要想一次包完全部，而是優先保：
- rebuild/search 契約
- tmp 清理
- rebuild 期間 query 不 crash
- history / apply 的最小正確性

### D. 路徑解析
這個專案已經明確踩過：
- `config.json` 相對路徑
- cache root 跟著 cwd 漂
- replace rules 相對路徑
- backups 污染 pytest 收集

這條線之後要持續保護，因為它很容易被不小心「順手改壞」。

---

## 7) 這個專案目前常用的測試類型

### 7.1 Import smoke
適合：
- module split
- façade 遷移
- re-export cleanup
- lazy import 調整

範例：

```bash
uv run python -c "from app.views.extractor_view import ExtractorView; print('ok')"
uv run python -c "from app.views.translation_view import TranslationView; print('ok')"
uv run python -c "import main; print('ok')"
```

### 7.2 Guard tests
適合：
- UI refactor 不應破壞某些架構規則
- main import 邊界不應回退

目前常用：
- `tests/test_main_imports.py`
- `tests/test_ui_refactor_guard.py`

### 7.3 Targeted behavior tests
適合：
- cache orchestration
- path resolution
- restore / search / history 這種局部行為

目前代表性檔案：
- `tests/test_cache_search_orchestration.py`
- `tests/test_cache_view_features.py`
- `tests/test_path_resolution.py`

---

## 8) 測試撰寫時的實務判斷

### 情境 A：這顆 PR 只是搬 import / split module
優先測：
- import smoke
- view import smoke
- `test_main_imports`
- `test_ui_refactor_guard`

### 情境 B：這顆 PR 動到 cache / search / history
優先測：
- cache orchestration 相關 targeted test
- 是否有 tmp 殘留
- rebuild / query 契約
- query / apply / restore 的最小流程

### 情境 C：這顆 PR 動到 path resolution
優先測：
- 不同 cwd 下仍可正確 resolve
- 相對 config / replace_rules / cache root 不漂移
- `pytest -q` 不被 `backups/` 污染

### 情境 D：這顆 PR 想「看起來順手」多清一些東西
先停一下，問自己：
- 這是同一責任線嗎？
- 還是只是因為現在剛好看到？

如果不是同一責任線，通常應拆成下一顆 PR，而不是一把梭。

---

## 9) 什麼叫「夠用的測試」

在這個專案裡，「夠用」不是測到天荒地老，而是：
- 有抓到這顆 PR 最可能壞掉的地方
- fail 時可以快速定位
- 沒有把完全不相關的區域拖進來
- 後續重構時能明確知道哪條契約不能破

也就是：
- 少而準，比多而散有價值

---

## 10) 不推薦的測試寫法

### 10.1 只測內部 helper，卻不測對外 contract
這樣很容易出現：
- helper 全綠
- 但 caller import 早就炸了

### 10.2 一顆 PR 只靠人工點 UI，不補回歸測試
這種短期看起來快，但下次再改就沒有保護網。

### 10.3 用太抽象的測試名稱
fail 時完全不知道在保什麼，等於白寫。

### 10.4 測試 scope 超出 PR 本身
例如：
- 只改低風險 caller migration
- 卻硬塞 cache heavy behavior test

這種會讓 checklist 失焦，也增加誤判成本。

---

## 11) 推薦工作流

寫這個專案的測試時，推薦順序：

1. 先想這顆 PR 最容易壞哪裡
2. 先補最小 smoke / contract test
3. 再補 1~2 顆最關鍵 behavior test
4. 跑最小驗證集
5. 再視風險補整包 `tests`

不是每顆 PR 都一開始就跑最重的那一套。

---

## 12) 最後總結

這個專案的測試哲學可以濃縮成一句：

**先保外部契約，再保最危險的行為邊界；少測內部長相，多測「改完還能不能活」。**

如果未來要延伸這份文件，建議優先補：
- façade migration 類 PR 的測試範例
- cache/history 類 PR 的測試範例
- path resolution 類 PR 的測試範例
