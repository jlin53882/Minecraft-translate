# PR27（設計）— Remove legacy external references to fix path resolution errors

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 類型：path resolution / legacy reference cleanup
> 本輪狀態：已盤點 / 已設計，準備實作

---

## 一句話總結

PR27 專門清理目前仍依賴舊版外部路徑解析行為的程式碼，重點是移除對 `os.getcwd()` 與相對 `config.json` / 相對資源路徑的歷史依賴，避免在 Restore/Backup 或從不同工作目錄啟動時，因舊路徑不存在而爆出路徑錯誤。

---

## 1) Phase 0 盤點

### 1.1 已確認的 legacy path 來源
1. `translation_tool/utils/config_manager.py`
- `load_config(config_path='config.json')`
- `save_config(config, config_path='config.json')`
- 目前預設吃相對路徑，會跟著 cwd 漂移

2. `translation_tool/utils/cache_manager.py`
- `_get_cache_root()` 目前用 `Path(os.getcwd())`
- `get_cache_overview()` 也用 `Path(os.getcwd()) / cache_dir_name`
- 這會讓 cache history / restore / backup 相關 UI 路徑跟著 cwd 飄移

3. `translation_tool/utils/species_cache.py`
- `initialize_species_cache()` 目前用 `Path(os.getcwd())`
- 會讓學名快取落到啟動目錄，而不是 repo 內穩定路徑

4. `translation_tool/utils/text_processor.py`
- `load_replace_rules(path)` / `save_replace_rules(path, ...)`
- `load_custom_translations(folder_path, ...)`
- 對相對路徑直接 `os.path.exists/open`，仍依賴 cwd

### 1.2 目前確認的影響面
- `main.py` 與多個 core module 直接呼叫 `load_config()`
- `cache_view.py` 的歷史 restore / backup 會依賴 `cache_root`
- FTB / lang merge / variant comparator / rules 等流程會依賴 `replace_rules_path`
- lookup / species cache 會依賴學名快取目錄

### 1.3 風險邊界
- 不碰 view UI 行為
- 不改 config schema
- 不改 QC/checkers 線
- 不改已完成的 `app/services.py` split 主線

### 1.4 Phase 0 實際盤點依據
- `config_manager.py` 目前預設 `config.json` 相對路徑
- `cache_manager.py` / `species_cache.py` 目前仍直接使用 `os.getcwd()`
- `text_processor.py` 對相對規則路徑與自訂翻譯路徑未做 project-root resolve
- `tests/test_cache_search_orchestration.py` 目前還靠 `monkeypatch.chdir(tmp_path)` 模擬舊行為，需同步調整

---

## 2) PR27 目標
- 建立穩定的 project-root 路徑解析 helper
- 移除目前核心路徑解析對 `os.getcwd()` 的依賴
- 讓 `load_config()` / `save_config()` / cache root / species cache / replace rules / custom translations 都改走 project-root-resolved 路徑
- 修正相應測試，驗證脫離 cwd 後仍正常

---

## 3) Scope / Out-of-scope

### Scope
- `translation_tool/utils/config_manager.py`
- `translation_tool/utils/cache_manager.py`
- `translation_tool/utils/species_cache.py`
- `translation_tool/utils/text_processor.py`
- `tests/test_cache_search_orchestration.py`
- 視需要新增 path resolution 測試

### Out-of-scope
- 不改 `app/views/*` UI 結構
- 不改 `config.json` schema
- 不改 `qc_view.py`
- 不改 pipeline split 既有檔案結構
- 不動 `backups/` 規則

---

## 4) 預計修改

### 4.1 `config_manager.py`
- 新增：
  - `get_project_root()`
  - `resolve_project_path(path_like)`
  - `CONFIG_PATH`
- `load_config()` / `save_config()` 改用 stable path resolve
- `setup_logging()` 若 `log_dir` 為相對路徑，也改為 project-root resolve

### 4.2 `cache_manager.py`
- `_get_cache_root()` 改用 `get_project_root()` / `resolve_project_path()`
- `get_cache_overview()` 的 `cache_root` 字串同步改為 stable resolved path
- 移除 `os.getcwd()` 依賴

### 4.3 `species_cache.py`
- `initialize_species_cache()` 改用 stable project-root resolve
- 移除 `os.getcwd()` 依賴

### 4.4 `text_processor.py`
- `load_replace_rules()` / `save_replace_rules()` 改為相對路徑時自動 resolve 到 project root
- `load_custom_translations()` 同樣改為 project-root resolve

### 4.5 tests
- 更新 `tests/test_cache_search_orchestration.py`
- 讓測試不要再靠 `monkeypatch.chdir(tmp_path)` 維持舊行為
- 補至少一個 path resolution 測試，證明脫離 cwd 後仍能正確讀設定 / 取得 cache root

---

## 5) Validation checklist
- [ ] `uv run python -c "from translation_tool.utils.config_manager import get_project_root, resolve_project_path, load_config; print(get_project_root()); print(resolve_project_path('config.json')); print(bool(load_config()))"`
- [ ] `uv run python -c "from translation_tool.utils import cache_manager; print(cache_manager._get_cache_root())"`
- [ ] `uv run python -c "from translation_tool.utils import species_cache; print(species_cache.initialize_species_cache())"`
- [ ] `uv run pytest -q tests/test_cache_search_orchestration.py`
- [ ] `uv run pytest -q tests`

---

## 6) 風險與 rollback

### 6.1 主要風險
1. 改完後某些測試仍偷偷依賴 cwd
2. 相對路徑 resolve 過頭，導致原本的絕對路徑設定被錯改
3. `species_cache.py` 在 import / initialize 時行為改變
4. `log_dir` 相對路徑改為 project-root resolve 後，寫檔位置與舊行為不同

### 6.2 rollback
- 回退：
  - `translation_tool/utils/config_manager.py`
  - `translation_tool/utils/cache_manager.py`
  - `translation_tool/utils/species_cache.py`
  - `translation_tool/utils/text_processor.py`
  - `tests/test_cache_search_orchestration.py`
  - 本次 PR 文件

---

## 7) 預期交付物
- 穩定的 project-root 路徑 helper
- 移除 legacy cwd 依賴
- 修正後可直接 `uv run pytest -q tests`
- `docs/pr/YYYY-MM-DD_HHmm_PR_pr27-remove-legacy-external-references.md`
