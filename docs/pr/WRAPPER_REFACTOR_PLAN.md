# 包裝層重構 PR 計劃

## 現況分析

### app/services_impl/ 包裝層

| PR | 內容 | 風險 |
|----|------|------|
| PR72 | config_service.py - 4 個包裝層重構 | 中 |
| PR73 | logging_service.py - 檢查是否過度包裝 | 低 |
| PR74 | cache/ 包裝層重構 | 中 |
| PR75 | pipelines/ 包裝層重構 | 高 |

### translation_tool/ 包裝層

| PR | 內容 | 風險 |
|----|------|------|
| PR76 | utils/ 包裝層重構 | 中 |
| PR77 | plugins/ 包裝層重構 | 中 |

### Docstring 補完計劃

| PR | 內容 | 數量 |
|----|------|------|
| PR78 | app/views/ 關鍵函數 docstring | ~100 |
| PR79 | translation_tool/core/ 關鍵函數 docstring | ~100 |
| PR80 | translation_tool/plugins/ 關鍵函數 docstring | ~100 |

---

## 詳細設計

### PR72: config_service.py 重構

**目標**：移除 4 個包裝層，直接調用底層函數

**現狀**：
```python
# config_service.py
def load_replace_rules():
    return load_rules_core(REPLACE_RULES_PATH)
```

**改為**：
1. 將 `REPLACE_RULES_PATH`, `CONFIG_PATH` 移到共用位置
2. 讓調用方直接 import 底層函數
3. 刪除包裝層

**影響範圍**：
- app/views/rules_view.py
- app/views/rules_actions.py
- app/views/config_view.py

---

### PR73: logging_service.py 檢查

**目標**：確認 LogLimiter 是否需要包裝

---

### PR78-80: Docstring 補完

**原則**：
- 關鍵公共 API 必須有 docstring
- 內部函數可省略
- 用一句話說明功能

---

## 執行順序

1. 先處理 PR72 (config_service)
2. 然後 PR78-80 (docstring)
3. 其他包裝層視風險決定
