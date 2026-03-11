# PR Title
Refactor: Remove legacy external references to fix path resolution errors

# PR Description

## Summary
本 PR 專門清理 PR1~25 遺留的舊版外部引用程式碼，重點是移除核心路徑解析對 `os.getcwd()` 與相對 `config.json` / 相對資源路徑的歷史依賴。

這次修改的目的不是改功能，而是解決並預防在 Restore/Backup、不同工作目錄啟動、或舊路徑已不存在時，因 legacy 外部引用造成的「路徑錯誤」問題，讓目前程式結構能獨立運作，不再偷偷依賴已移除或不穩定的舊路徑行為。

---

## Phase 1 完成清單
- [x] 做了：完成 PR27 Phase 0 盤點，確認 legacy path 問題集中在 `config_manager.py`、`cache_manager.py`、`species_cache.py`、`text_processor.py`
- [x] 做了：修改前先建立可回退備份，且備份放進 `backups/pr27-remove-legacy-external-references-20260312-0032/`
- [x] 做了：在 `translation_tool/utils/config_manager.py` 新增穩定的 project-root 路徑 helper，並讓 `load_config()` / `save_config()` 改走穩定解析
- [x] 做了：修正 `translation_tool/utils/cache_manager.py`，移除 `os.getcwd()` 依賴，改由 project-root resolve 取得 cache root
- [x] 做了：修正 `translation_tool/utils/species_cache.py`，移除 `os.getcwd()` 依賴，改由 project-root resolve 存放學名快取
- [x] 做了：修正 `translation_tool/utils/text_processor.py`，讓 `replace_rules` / `custom_translations` 的相對路徑自動 resolve 到 project root
- [x] 做了：更新 `tests/test_cache_search_orchestration.py`，移除對舊 cwd 行為的依賴
- [x] 做了：新增 `tests/test_path_resolution.py`，驗證 config / cache / replace_rules 的路徑解析不再跟著 cwd 漂移
- [x] 做了：完成 Validation checklist 內所有指令驗證
- [ ] 未做：QC / checkers 線處理
- [ ] 未做：任何 view UI 結構改版
- [ ] 未做：任何 config schema 變更

---

## 刪除/移除/替換說明（若有，固定放這裡）

### 替換項目：`translation_tool/utils/config_manager.py` 內對相對 `config.json` 的 legacy 依賴
- **為什麼改**：舊版 `load_config(config_path='config.json')` / `save_config(config_path='config.json')` 會跟著 cwd 漂移，導致從不同啟動位置執行時，程式可能去讀不存在的舊路徑。
- **為什麼能刪**：現在已有明確 repo 結構，`config.json` 的 canonical 位置是 repo root；改成由 `config_manager.py` 自己解析 project root，能消除對外部工作目錄的隱性依賴。
- **目前誰在用 / 沒人在用**：`main.py` 與多個 core module 都直接呼叫 `load_config()`；沒有 caller 需要依賴「跟著 cwd 變動的 config 路徑」這種舊行為。
- **替代路徑是什麼**：新增 `get_project_root()`、`CONFIG_PATH`、`resolve_project_path()`；`load_config()` / `save_config()` 改走這組 helper。
- **風險是什麼**：若有外部流程刻意靠 cwd 切換 config 檔，這次會改變其行為；但以目前 repo 結構與 app/services split 後的設計來看，穩定 project root 才是正確行為。
- **我是怎麼驗證的**：新增 `tests/test_path_resolution.py::test_load_config_uses_project_root_not_cwd`，並實跑 `uv run python -c "from translation_tool.utils.config_manager import ..."` 與 `uv run pytest -q tests`。

### 替換項目：`translation_tool/utils/cache_manager.py` 內以 `os.getcwd()` 計算 cache root 的 legacy 行為
- **為什麼改**：cache root 若跟著 cwd 變動，cache history / restore / backup 相關路徑就可能指向錯位置，進而在舊路徑不存在時爆出錯誤。
- **為什麼能刪**：現在 cache root 的真正基準應該是 repo root + config 內的 `translator.cache_directory`，不是執行當下的工作目錄。
- **目前誰在用 / 沒人在用**：`cache_view.py` 的 history / restore 流程、`cache_search_orchestration` 測試、以及 cache overview 都會依賴 `cache_root`；沒有正式 caller 應再依賴 `os.getcwd()`。
- **替代路徑是什麼**：`_get_cache_root()` 與 `get_cache_overview()` 改走 `resolve_project_path(cache_dir_name)`。
- **風險是什麼**：舊測試如果靠 `monkeypatch.chdir(tmp_path)` 模擬根目錄會失效，因此本 PR 已同步更新測試。
- **我是怎麼驗證的**：更新 `tests/test_cache_search_orchestration.py`、新增 `tests/test_path_resolution.py::test_cache_root_uses_project_root_not_cwd`，並實跑 `uv run pytest -q tests/test_cache_search_orchestration.py tests`。

### 替換項目：`translation_tool/utils/species_cache.py` 內以 `os.getcwd()` 計算學名快取目錄的 legacy 行為
- **為什麼改**：學名快取目錄若跟著 cwd 變動，會導致快取落到非預期位置，或去找已不存在的舊資料夾。
- **為什麼能刪**：學名快取應與專案根目錄綁定，不應依賴外部啟動目錄。
- **目前誰在用 / 沒人在用**：lookup / species cache 流程會用；沒有正式 caller 需要舊 cwd 行為。
- **替代路徑是什麼**：改由 `resolve_project_path(cache_dir_name)` 建立 `_CACHE_DIR`。
- **風險是什麼**：若外部手動把學名資料庫放在某個依賴 cwd 的位置，這次行為會改變；但以 repo 一致性與可回復性來看，固定到 project root 才穩。
- **我是怎麼驗證的**：實跑 `uv run python -c "from translation_tool.utils import species_cache; print(species_cache.initialize_species_cache())"`。

### 替換項目：`translation_tool/utils/text_processor.py` 內對相對 `replace_rules` / `custom_translations` 路徑的 legacy 依賴
- **為什麼改**：舊版對相對路徑直接 `os.path.exists/open`，會跟著 cwd 漂移，導致規則檔或自訂翻譯表在不同啟動目錄下被誤判不存在。
- **為什麼能刪**：這些相對資源本質上是專案內資源，應一律以 project root resolve。
- **目前誰在用 / 沒人在用**：FTB、lang merger、variant comparator、rules 流程都會用到 `replace_rules_path` 或 `custom_translator_folder`；沒有 caller 需要舊 cwd 行為。
- **替代路徑是什麼**：`load_replace_rules()` / `save_replace_rules()` / `load_custom_translations()` 改走 `resolve_project_path(...)`。
- **風險是什麼**：若使用者刻意傳絕對路徑，不能被誤改；本次 helper 對絕對路徑會原樣保留，因此風險可控。
- **我是怎麼驗證的**：新增 `tests/test_path_resolution.py::test_replace_rules_relative_path_resolves_to_project_root`，並實跑 `uv run pytest -q tests`。

### 刪除項目：`tests/test_cache_search_orchestration.py` 對 `monkeypatch.chdir(tmp_path)` 的舊行為依賴
- **為什麼改**：該測試原本是為了配合 `cache_manager` 舊版 `os.getcwd()` 行為而存在；一旦路徑解析改正，測試就不能再用 cwd 模擬 project root。
- **為什麼能刪**：新的正式行為是 project-root resolve，不再以 cwd 當 canonical root；測試應改成 patch helper，而不是 patch cwd。
- **目前誰在用 / 沒人在用**：只有該測試自己在依賴這個舊行為，正式 runtime 不應再依賴。
- **替代路徑是什麼**：改成 `monkeypatch.setattr(cache_manager, "resolve_project_path", lambda p: tmp_path / p)`。
- **風險是什麼**：若 patch 點選錯，測試可能變成假綠；因此本次有再跑整包 `tests` 驗證。
- **我是怎麼驗證的**：`uv run pytest -q tests/test_cache_search_orchestration.py tests` 全通過。

---

## What was done

### 1. 在 `config_manager.py` 建立穩定路徑解析基礎
新增：
- `get_project_root()`
- `PROJECT_ROOT`
- `CONFIG_PATH`
- `resolve_project_path(path_like)`

並讓：
- `load_config()`
- `save_config()`
- `setup_logging()`

全部改走 project-root-based resolve。

這樣做之後：
- `config.json` 不再跟著 cwd 漂移
- 相對 `log_dir` 也會落在專案底下的穩定位置
- 未來其他模組若有相對路徑，也能共用這組 helper

### 2. 拔掉 `cache_manager.py` 與 `species_cache.py` 的 cwd 依賴
本次將兩個核心模組的 `Path(os.getcwd())` 行為改掉：
- `cache_manager._get_cache_root()`
- `cache_manager.get_cache_overview()` 內的 `cache_root`
- `species_cache.initialize_species_cache()`

改完後：
- cache root
- history / restore / backup 相關路徑
- species cache 目錄

都改為由 project root 解析，不再因外部執行位置不同而飄移。

### 3. 修正 `text_processor.py` 的相對資源路徑
本次同步修正：
- `load_replace_rules(path)`
- `save_replace_rules(path, rules)`
- `load_custom_translations(folder_path, filename="table.tsv")`

行為改為：
- 絕對路徑：原樣使用
- 相對路徑：自動 resolve 到 project root

這樣可以避免：
- `replace_rules.json`
- `custom_translators/table.tsv`

在不同工作目錄啟動時被誤判不存在。

### 4. 更新與新增測試
更新：
- `tests/test_cache_search_orchestration.py`

新增：
- `tests/test_path_resolution.py`
  - 驗證 `load_config()` 不再跟著 cwd 漂
  - 驗證 `cache_root` 不再跟著 cwd 漂
  - 驗證 `replace_rules.json` 相對路徑會 resolve 到 project root

這次不是只改碼沒驗證，而是把舊路徑解析問題直接變成可回歸檢查的測試。

### 5. 備份位置
本次修改前已建立可回退備份：
- `backups/pr27-remove-legacy-external-references-20260312-0032/translation_tool/utils/config_manager.py`
- `backups/pr27-remove-legacy-external-references-20260312-0032/translation_tool/utils/cache_manager.py`
- `backups/pr27-remove-legacy-external-references-20260312-0032/translation_tool/utils/species_cache.py`
- `backups/pr27-remove-legacy-external-references-20260312-0032/translation_tool/utils/text_processor.py`
- `backups/pr27-remove-legacy-external-references-20260312-0032/tests/test_cache_search_orchestration.py`
- `backups/pr27-remove-legacy-external-references-20260312-0032/docs/pr/2026-03-12_0030_PR_pr27-remove-legacy-external-references-design.md`

---

## Important findings
- 真正的路徑錯誤源頭不是單一檔案，而是一整串 legacy 假設：
  - config 預設相對 `config.json`
  - cache root 依賴 `os.getcwd()`
  - species cache 依賴 `os.getcwd()`
  - replace rules / custom translations 相對路徑直接吃 cwd
- `cache_view` 的 restore/history 看起來像 UI 問題，但根因其實在更底層的 `cache_root` 解析。
- 若只修其中一個點，剩下的舊相對路徑還是會繼續埋雷；所以 PR27 必須一次把同一類 legacy path reference 收掉。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有修改 `app/views/*` UI 結構
- 沒有修改 QC / checkers 線
- 沒有修改 `config.json` schema
- 沒有改動 `backups/` 規則
- 沒有碰 `app/services.py` split 主線結構

---

## Next step
- 若後續仍有 restore / backup / path 類錯誤，優先檢查是否還有其他 module 直接依賴 cwd
- 若要繼續收尾，可考慮補一顆小 PR，盤點是否還有其他相對資源路徑未走 `resolve_project_path()`

---

## Validation checklist
- [x] `uv run python -c "from translation_tool.utils.config_manager import get_project_root, resolve_project_path, load_config; print(get_project_root()); print(resolve_project_path('config.json')); print(bool(load_config()))"`
- [x] `uv run python -c "from translation_tool.utils import cache_manager; print(cache_manager._get_cache_root())"`
- [x] `uv run python -c "from translation_tool.utils import species_cache; print(species_cache.initialize_species_cache())"`
- [x] `uv run pytest -q tests/test_cache_search_orchestration.py`
- [x] `uv run pytest -q tests`

---

## Test result
```text
$ uv run python -c "from translation_tool.utils.config_manager import get_project_root, resolve_project_path, load_config; print(get_project_root()); print(resolve_project_path('config.json')); print(bool(load_config()))"
C:\Users\admin\Desktop\minecraft_translator_flet
C:\Users\admin\Desktop\minecraft_translator_flet\config.json
True

$ uv run python -c "from translation_tool.utils import cache_manager; print(cache_manager._get_cache_root())"
C:\Users\admin\Desktop\minecraft_translator_flet\快取資料

$ uv run python -c "from translation_tool.utils import species_cache; print(species_cache.initialize_species_cache())"
True

$ uv run pytest -q tests/test_cache_search_orchestration.py
...                                                                      [100%]
3 passed in 0.48s

$ uv run pytest -q tests
........................................                                 [100%]
40 passed in 1.35s
```
