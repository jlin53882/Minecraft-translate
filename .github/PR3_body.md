## 概述

擴展 styled_card 元件，增加卡片收合功能。

## 變更

### 修改檔案
- `app/ui/components.py` - styled_card 函數

## 新增功能

- `collapsible` 參數：是否支援收合
- `default_collapsed` 參數：預設收合狀態
- `quick_actions` 參數：快速操作按鈕（預留）

## 使用方式

```python
# 基本用法（向後相容）
styled_card(title="設定", icon=ft.Icons.SETTINGS, content=...)

# 支援收合
styled_card(
    title="進階設定",
    icon=ft.Icons.SETTINGS,
    content=...,
    collapsible=True,
    default_collapsed=True,
)
```

## 驗收清單

- [x] 語法檢查通過
- [ ] 收合功能正常
- [ ] 向後相容
