## 概述

新增統一狀態元件，用於 Loading / Empty / Error 狀態顯示。

## 變更

### 修改檔案
- `app/ui/components.py` - 新增三個狀態函數

## 新增功能

- `loading_state()` - 統一的載入狀態
- `empty_state()` - 統一的空資料狀態
- `error_state()` - 統一的錯誤狀態

## 使用方式

```python
from app.ui.components import loading_state, empty_state, error_state

# 載入中
page.add(loading_state("正在載入資料..."))

# 空資料
page.add(empty_state(
    icon=ft.Icons.FOLDER_OPEN,
    title="沒有資料",
    message="請先新增資料"
))

# 錯誤
page.add(error_state(
    icon=ft.Icons.ERROR,
    title="發生錯誤",
    message="無法載入資料，請稍後再試"
))
```

## 驗收清單

- [x] 語法檢查通過
- [ ] 各狀態顯示正常
