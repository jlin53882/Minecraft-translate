# PR Title
fix: replace `os.getcwd()` with project-root-based paths in `app/services.py`

# PR Description

## Summary
PR8 的目標是移除 `app/services.py` 對目前 shell 工作目錄（`cwd`）的隱性依賴，避免從不同目錄、IDE、快捷方式或測試環境啟動時，`config.json` / `replace_rules.json` 指到錯誤位置。

這顆 PR 只改 `app/services.py`，不改其他模組邏輯。

---

## 問題背景
原本 `app/services.py` 內有兩個路徑定義：

- `CONFIG_PATH = os.path.join(os.getcwd(), "config.json")`
- `REPLACE_RULES_PATH = os.path.join(os.getcwd(), "replace_rules.json")`

這代表：
- 只要不是在 repo 根目錄啟動
- 或是由其他入口切換了工作目錄

就可能讀到錯的設定檔路徑。

---

## 設計決策

### 1. 改用 project-root-based path
使用：

- `PROJECT_ROOT = Path(__file__).resolve().parents[1]`

再導出：

- `CONFIG_PATH = str(PROJECT_ROOT / "config.json")`
- `REPLACE_RULES_PATH = str(PROJECT_ROOT / "replace_rules.json")`

### 2. 維持 backward compatible
下游 `load_config()` / `save_config()` / replace rules 讀寫本來就接受字串路徑，所以改成穩定絕對路徑不會破壞既有邏輯。

### 3. 對齊專案既有慣例
測試檔已經普遍使用：
- `Path(__file__).resolve().parents[...]`

因此這次修正不是引入新風格，而是把 `services.py` 拉回既有慣例。

---

## Scope

### In scope
- `app/services.py`
- `CONFIG_PATH`
- `REPLACE_RULES_PATH`

### Out of scope
- `config_manager.py` 邏輯重寫
- 任何設定格式變更
- UI / cache / translation 流程邏輯調整

---

## 實作內容

### Before
```python
CONFIG_PATH = os.path.join(os.getcwd(), "config.json")
REPLACE_RULES_PATH = os.path.join(os.getcwd(), "replace_rules.json")
```

### After
```python
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = str(PROJECT_ROOT / "config.json")
REPLACE_RULES_PATH = str(PROJECT_ROOT / "replace_rules.json")
```

---

## Validation checklist

- [x] `uv run pytest` → **27 passed**
- [x] `uv run python -c "from app.services import CONFIG_PATH, REPLACE_RULES_PATH; print(CONFIG_PATH); print(REPLACE_RULES_PATH)"`
- [x] 路徑確認指向：
  - `C:\Users\admin\Desktop\minecraft_translator_flet\config.json`
  - `C:\Users\admin\Desktop\minecraft_translator_flet\replace_rules.json`
- [x] 變更範圍僅 `app/services.py`

---

## Risk assessment

### 主要風險
- 幾乎沒有功能性風險，因為只是把原本不穩定的相對路徑，換成穩定的絕對路徑

### 真正避免的風險
- 從非 repo 根目錄啟動時讀錯 config
- 測試 / IDE / background task 因 cwd 不同而出現隱性錯誤

---

## One-line summary
PR8 是一顆小而值錢的穩定性修復：不改功能，只把 `app/services.py` 的設定檔路徑從依賴 `cwd` 改成依賴專案根目錄，避免從不同啟動位置執行時讀錯檔。
