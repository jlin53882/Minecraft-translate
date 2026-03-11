# PR15（設計）— 抽離 config / rules IO 到 `services_impl/config_service.py`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 前置：
> - PR13：已建立 `app/services_impl/` 骨架
> - PR14：已抽離 logging 到 `app/services_impl/logging_service.py`
> 本輪狀態：已實作 / 已驗證（PR15 落地：抽離 config/rules IO；不改 pipeline/service 流程）。

---

## 一句話總結

`app/services.py` 目前同時承擔「服務入口」與「config/replace_rules 的路徑常數 + 讀寫 wrapper」；PR15 建議把這段 IO concern 抽離到 `app/services_impl/config_service.py`，並由 `app/services.py` re-export 保持 UI caller (`from app.services import load_config_json/save_config_json/load_replace_rules/save_replace_rules`) 完全相容。

---

## 1) Phase 0 盤點：config / rules / 路徑常數目前的分布

### 1.1 相關常數與 wrapper（在 `app/services.py`）
- 路徑常數：
  - `PROJECT_ROOT = Path(__file__).resolve().parents[1]`
  - `CONFIG_PATH = str(PROJECT_ROOT / "config.json")`
  - `REPLACE_RULES_PATH = str(PROJECT_ROOT / "replace_rules.json")`
- 搬移後的路徑注意事項：
  - 若這些常數搬到 `app/services_impl/config_service.py`，`__file__` 的所在層級會改變。
  - 以 `config_service.py` 而言，`Path(__file__).resolve().parents[1]` 只會回到 `.../app`，不是專案根目錄。
  - 因此 PR15 實作時必須改成 `Path(__file__).resolve().parents[2]`，才能正確回到 project root。
- config wrapper：
  - `_load_app_config()`：`load_config(CONFIG_PATH)`
  - `_save_app_config(config)`：`save_config(config, CONFIG_PATH)`
  - `load_config_json()` / `save_config_json(config)`：薄包裝（供 UI view 使用）
- rules wrapper：
  - `load_replace_rules()`：`load_rules_core(REPLACE_RULES_PATH)`
  - `save_replace_rules(rules)`：`save_rules_core(REPLACE_RULES_PATH, rules)`

> 注意：PR8 已把 `CONFIG_PATH/REPLACE_RULES_PATH` 從 `os.getcwd()` 改成 `PROJECT_ROOT`，這個穩定性決策必須保留。

### 1.2 哪些 service 入口依賴 config wrapper（直接/間接）
- 直接依賴：
  - `update_logger_config()` 會呼叫 `_load_app_config()` 讀 logging 設定（PR14 後仍由 services.py 傳入 loader）
- UI views 依賴（直接 import）：
  - `app/views/config_view.py`：`load_config_json()` / `save_config_json()`
  - `app/views/rules_view.py`：`load_replace_rules()` / `save_replace_rules()`
  - `app/views/bundler_view.py`：`load_config_json()`

---

## 2) PR15 設計

### 2.1 目標
- 把 config/rules IO concern 從 `app/services.py` 抽離到：
  - `app/services_impl/config_service.py`
- `app/services.py` 對外 API 維持不變（façade/re-export）

### 2.2 Scope（In scope）
- 新增 `app/services_impl/config_service.py`，承接：
  - `PROJECT_ROOT` / `CONFIG_PATH` / `REPLACE_RULES_PATH`
  - `load_config_json()` / `save_config_json()`
  - `load_replace_rules()` / `save_replace_rules()`
  - （可選）保留 `_load_app_config()` / `_save_app_config()` 作為內部 helper（但是否搬入需在實作時確認）

- 修改 `app/services.py`：
  - 改為 `from app.services_impl.config_service import ...` 並 re-export 同名符號
  - 保持原有 name/行為（避免 UI caller 破壞）

### 2.3 Out-of-scope（本 PR 不做）
- 不改 config 結構、不改 `translation_tool.utils.config_manager` 的 merge 規則
- 不調整 views 的呼叫方式（讓它們保持原 import）
- 不改任何 pipeline/service 流程

### 2.4 建議新增/修改檔案
- 新增：`app/services_impl/config_service.py`
- 修改：`app/services.py`
- （可選）修改：`app/services_impl/__init__.py`（若要提供統一入口，但非必要）

### 2.5 façade / re-export 相容策略
- PR15 後，仍保證以下 import 不用改：
  - `from app.services import load_config_json, save_config_json`
  - `from app.services import load_replace_rules, save_replace_rules`
- 另外，若 caller 透過 `import app.services as s` 取 `s.CONFIG_PATH`，也應保持成立。
- 依賴方向要明確固定為：
  - `app/services.py` → 依賴 → `app/services_impl/config_service.py`
  - `app/services.py` → 依賴 → `app/services_impl/logging_service.py`
- `logging_service.py` 不應反向 import `config_service.py`。
  - 建議做法：維持 PR14 的 `update_logger_config(config_loader, ...)` 介面不變，由 `app/services.py` 傳入 `_load_app_config`（或其 alias）。
  - 這樣可以避免 `logging_service ↔ config_service` 形成雙向耦合或 circular import。

### 2.6 驗證方式（PR15 預計）
- import/smoke：
  - `uv run python -c "import app.services as s; print('ok', s.CONFIG_PATH, s.REPLACE_RULES_PATH)"`
  - `uv run python -c "from app.services_impl import config_service as cs; print('ok', cs.CONFIG_PATH)"`
- pytest（最低集合）：
  - `uv run pytest -q tests/test_main_imports.py`
  - （建議）加 `uv run pytest -q tests/test_ui_refactor_guard.py`

### 2.7 風險與 rollback
- 風險：
  - 路徑常數回退到 cwd（不能發生）：必須維持 PR8 的 project-root-based path；搬移到 `config_service.py` 後需改用 `Path(__file__).resolve().parents[2]`。
  - circular import：config_service 必須保持純 IO/wrapper，不 import views，也不應 import logging_service。
  - `update_logger_config()` 仍需要可取得 config loader：PR15 實作時應由 `services.py` 傳入 loader，避免讓 `logging_service.py` 直接依賴 `config_service.py`。
- rollback：
  - 由於對外 API 不變，回退 PR15 即可恢復原狀

---

## 3) 本輪新增文件
- `docs/pr/2026-03-11_2155_PR_pr15-config-service-split-design.md`

---

## 4) 本輪驗證（已實作 / 已驗證）
- import/smoke：
  - `uv run python -c "import app.services as s; from app.services_impl import config_service as cs; print('same_paths', s.CONFIG_PATH==cs.CONFIG_PATH, s.REPLACE_RULES_PATH==cs.REPLACE_RULES_PATH)"`
  - `uv run python -c "import app.services as s; print('paths', s.CONFIG_PATH, s.REPLACE_RULES_PATH)"`
  - `uv run python -c "from app.services_impl import config_service as cs; print('root', cs.PROJECT_ROOT)"`

- pytest（最低集合）：
  - `uv run pytest -q tests/test_main_imports.py tests/test_ui_refactor_guard.py`

- views 最小 import smoke（避免 config/rules view 因 import path 變動而掛掉）：
  - `uv run python -c "from app.views.config_view import ConfigView; from app.views.rules_view import RulesView; print('ok')"`

---

## 5) 需要家豪先決策的點
1. PR15 是否要把 `_load_app_config/_save_app_config` 也一併搬到 config_service，並在 services.py 留 alias？
   - 建議：可以搬（讓 concern 更集中），但要注意 PR14 的 `update_logger_config()` 目前仍在 services.py 取 loader；實作時需確保不引入額外耦合。
