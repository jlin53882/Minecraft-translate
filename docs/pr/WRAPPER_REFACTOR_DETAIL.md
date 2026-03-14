# 包裝層重構 PR 設計稿

> 版本：v1.0
> 編寫日期：2026-03-14

---

## PR72: 移除 load_replace_rules 包裝層

### 現況

```python
# app/services_impl/config_service.py
def load_replace_rules():
    """載入替換規則。"""
    return load_rules_core(REPLACE_RULES_PATH)  # REPLACE_RULES_PATH 是模組常數
```

### 調用點分析

| 檔案 | 調用次數 |
|------|----------|
| app/views/rules_view.py | 1 次 |
| app/views/rules/rules_actions.py | 1 次 |
| **總計** | **2 次** |

### 重構方案

1. 將 `REPLACE_RULES_PATH` 移至 `translation_tool/utils/text_processor.py`
2. 修改 `rules_view.py` 和 `rules_actions.py` 直接調用 `text_processor.load_replace_rules()`
3. 刪除 `config_service.load_replace_rules()`

### Phase 1: 現況分析 ✅
- [x] 找出調用點：2 個
- [x] 確認影響範圍：rules_view, rules_actions

### Phase 2: 實作
- [ ] 修改 text_processor.py（加入 REPLACE_RULES_PATH）
- [ ] 修改 rules_view.py
- [ ] 修改 rules_actions.py
- [ ] 刪除 config_service.load_replace_rules()
- [ ] 測試驗證
- [ ] Push

---

## PR73: 移除 save_replace_rules 包裝層

### 現況

```python
# app/services_impl/config_service.py
def save_replace_rules(rules):
    """儲存替換規則。"""
    save_rules_core(REPLACE_RULES_PATH, rules)
```

### 調用點分析

| 檔案 | 調用次數 |
|------|----------|
| app/views/rules/rules_actions.py | 1 次 |

### 重構方案

1. 修改 `rules_actions.py` 直接調用 `text_processor.save_replace_rules(REPLACE_RULES_PATH, rules)`
2. 刪除 `config_service.save_replace_rules()`

### Phase 1: 現況分析 ✅
- [x] 找出調用點：1 個
- [x] 確認影響範圍：rules_actions

### Phase 2: 實作
- [ ] 修改 rules_actions.py
- [ ] 刪除 config_service.save_replace_rules()
- [ ] 測試驗證
- [ ] Push

---

## PR74: 移除 load_config_json 包裝層

### 現況

```python
# app/services_impl/config_service.py
def load_config_json():
    """載入應用程式設定。"""
    return _load_app_config()  # 內部調用 load_config(CONFIG_PATH)
```

### 調用點分析

| 檔案 | 調用次數 |
|------|----------|
| app/views/config_view.py | 1 次 |

### 重構方案

1. 修改 `config_view.py` 直接調用底層
2. 刪除 `config_service.load_config_json()`

### Phase 1: 現況分析 ✅
- [x] 找出調用點：1 個
- [x] 確認影響範圍：config_view

### Phase 2: 實作
- [ ] 修改 config_view.py
- [ ] 刪除 config_service.load_config_json()
- [ ] 測試驗證
- [ ] Push

---

## PR75: 移除 save_config_json 包裝層

### 現況

```python
# app/services_impl/config_service.py
def save_config_json(config):
    """儲存應用程式設定。"""
    _save_app_config(config)  # 內部調用 save_config(config, CONFIG_PATH)
```

### 調用點分析

| 檔案 | 調用次數 |
|------|----------|
| app/views/config_view.py | 1 次 |

### 重構方案

1. 修改 `config_view.py` 直接調用底層
2. 刪除 `config_service.save_config_json()`

### Phase 1: 現況分析 ✅
- [x] 找出調用點：1 個
- [x] 確認影響範圍：config_view

### Phase 2: 實作
- [ ] 修改 config_view.py
- [ ] 刪除 config_service.save_config_json()
- [ ] 測試驗證
- [ ] Push

---

## PR76: Docstring 補完 - app/ui/ + app/services_impl/

### 目標

為以下檔案的公共函數補充 docstring：

| 檔案 | 函數 |
|------|------|
| app/ui/components.py | primary_button, secondary_button, create_snackbar |
| app/ui/theme.py | 全部導出 |
| app/services_impl/config_service.py | PROJECT_ROOT, CONFIG_PATH, REPLACE_RULES_PATH |
| app/services_impl/logging_service.py | LogLimiter, GLOBAL_LOG_LIMITER, UI_LOG_HANDLER |

### Phase 1: 現況分析
- [ ] 找出缺少 docstring 的公共函數
- [ ] 評估是否需要 docstring

### Phase 2: 實作
- [ ] 補充 docstring
- [ ] 測試驗證
- [ ] Push

---

## PR77-80: Docstring 補完 - 其他模組

### PR77: translation_tool/utils/
- config_manager.py
- text_processor.py
- cache_store.py
- exceptions.py

### PR78: translation_tool/core/
- lm_translator.py
- lang_merger.py
- jar_processor.py

### PR79: translation_tool/plugins/
- kubejs/*.py
- ftbquests/*.py
- md/*.py

### PR80: app/views/
- 關鍵 View 類別的公共方法
