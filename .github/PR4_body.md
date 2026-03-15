## 概述

新增進度條統一元件，用於長時間操作的視覺反饋。

## 變更

### 修改檔案
- `app/ui/components.py` - 新增 ProgressCard 類別

## 功能

- 顯示進度百分比
- 顯示 ETA（預估剩餘時間）
- 支援取消操作
- 統一外觀樣式

## 使用方式

```python
# 建立進度條卡片
progress = ProgressCard(
    title="正在翻譯...",
    current=50,
    total=100,
    on_cancel=lambda: print("取消"),
)
progress.start()

# 更新進度
progress.current = 75

# 設定狀態文字
progress.set_status("正在處理第 75 項...")
```

## 驗收清單

- [x] 語法檢查通過
- [ ] 進度條顯示正常
- [ ] ETA 計算正常
