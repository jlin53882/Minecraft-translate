## 概述

新增快速跳轉面板，讓使用者可以快速搜尋並跳轉到目標頁面。

## 變更

### 新增檔案
- `app/ui/quick_jump.py` - 快速跳轉面板模組

### 修改檔案
- `main.py` - 集成快速跳轉功能
- `app/ui/keyboard_shortcuts.py` - 新增 Ctrl+P 快捷鍵

## 功能

- 點擊 NavigationRail 上的搜尋圖標開啟面板
- 輸入關鍵字搜尋頁面名稱
- 支援模糊匹配
- 顯示每個頁面對應的快捷鍵
- 按 Enter 跳轉到第一個結果
- 按 Esc 關閉面板
- Ctrl+P 快捷鍵開啟面板

## 測試

```bash
.venv\Scripts\python test_main.py
```
用瀏覽器打開 http://localhost:8550 測試：
1. 點擊 NavigationRail 上的搜尋圖標
2. 輸入關鍵字測試搜尋
3. 按 Ctrl+P 測試快捷鍵

## 驗收清單

- [x] 語法檢查通過
- [ ] Web 測試正常運作
- [ ] 搜尋功能正常
- [ ] 快捷鍵正常
