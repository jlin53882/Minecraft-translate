## 概述

新增鍵盤快捷鍵系統，讓使用者可以用鍵盤快速操作。

## 變更

### 新增檔案
- `app/ui/keyboard_shortcuts.py` - 鍵盤快捷鍵模組
- `test_main.py` - Web 測試入口

### 修改檔案
- `main.py` - 註冊鍵盤事件處理

## 功能

- `Ctrl+1~9` - 快速跳轉頁面
- `Ctrl+F` - 聚焦搜尋框（預留，需各頁面綁定）
- `Ctrl+S` - 儲存（預留，需各頁面綁定）
- `Ctrl+R` - 重新整理（預留）

## 測試

```bash
.venv\Scripts\python test_main.py
```
然後用瀏覽器打開 http://localhost:8550 測試按 Ctrl+1~9 跳轉頁面。

## 驗收清單

- [x] 語法檢查通過
- [ ] Web 測試正常運作
- [ ] Console 無錯誤
