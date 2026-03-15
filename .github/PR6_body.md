## 概述

CacheView 重構的第一步 - 建立面板拆分結構。

## 變更

### 新增檔案
- `app/views/cache_manager/__init__.py` - 模組導出
- `app/views/cache_manager/panels/__init__.py` - 面板導出
- `app/views/cache_manager/panels/overview_panel.py` - 總覽面板
- `app/views/cache_manager/panels/query_panel.py` - 查詢面板
- `app/views/cache_manager/panels/shard_panel.py` - 分片面板

## 目標

將超過 3000 行的 CacheView 拆分為多個獨立 Panel：
- 每個 Panel 控制在 300 行以內
- 採用 MVP 模式分離關注點
- 未來可以逐步將舊程式碼遷移到新結構

## 驗收清單

- [x] 語法檢查通過
- [ ] 面板結構正確
- [ ] 預留擴展點
