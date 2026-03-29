# PR69 設計稿：主題系統建立

> 版本：v2.0（重新定位版）  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
建立統一的主題系統，管理全應用程式的顏色與樣式。

### 動機
- 經過驗證發現：**PR59 沒有建立獨立的主題模組**
- main.py 只有基本 theme 設定
- 各 view 存在大量 ft.Colors hardcode
- **重新定位**：從「收斂」改為「建立」主題系統

---

## 2. 現狀分析

### 2.1 當前主題狀態
- main.py 只有基本 Flet theme 設定
- **沒有獨立的主題模組**（如 theme.py）
- 各 view 存在大量 ft.Colors hardcode

### 2.2 Hardcode 統計
```powershell
findstr /s /r "ft\.Colors\." app\views\*.py
# 輸出：多個 view 有 hardcode 顏色
```

### 2.3 需建立的項目
1. 主題常數統一管理
2. Dark/Light mode 切換
3. 主題與各 view 的整合

---

## 3. 預期改動

### 3.1 建立 app/ui/theme.py

```python
"""主題系統統一管理。"""

from flet import Colors

# 主題顏色
PRIMARY_COLOR = Colors.BLUE_700
SECONDARY_COLOR = Colors.BLUE_GREY_700
SUCCESS_COLOR = Colors.GREEN_700
ERROR_COLOR = Colors.RED_700
WARNING_COLOR = Colors.ORANGE_700

# 主題切換
DARK_BG = Colors.GREY_900
LIGHT_BG = Colors.WHITE

# 按鈕樣式
BUTTON_HEIGHT = 42
BUTTON_RADIUS = 6
```

### 3.2 建立 Theme Manager

```python
class ThemeManager:
    """主題管理器。"""
    
    def __init__(self):
        self._is_dark = False
    
    @property
    def is_dark(self):
        return self._is_dark
    
    def toggle(self):
        self._is_dark = not self._is_dark
    
    def get_colors(self):
        """取得當前主題顏色。"""
        if self._is_dark:
            return {...}  # Dark mode colors
        return {...}  # Light mode colors
```

### 3.3 更新 View 使用主題

原則：
- 使用主題常數而非 hardcode
- 保持向後相容（fallback 到預設值）

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| 主題改變導致 UI 異常 | 🟠 中 | 先在測試環境驗證 |
| 漏掉某個 hardcode | 🟢 低 | 逐步替換，驗證後再下一個 |
| Dark/Light mode 不一致 | 🟠 中 | 兩種模式都要測試 |
| 與現有 code 衝突 | 🟠 中 | 保持 fallback 機制 |

---

## 5. Validation checklist

- [ ] 建立 `app/ui/theme.py` 主題常數
- [ ] 建立 ThemeManager 類別
- [ ] 更新至少 3 個 view 使用主題常數
- [ ] 測試 Light mode 正常
- [ ] 測試 Dark mode 正常
- [ ] `uv run pytest -q` - 確認測試通過

---

## 6. Rejected approaches

- 試過：不建立主題系統，保持現狀
- 為什麼放棄：長期維護困難，顏色無法統一管理
- 最終改採：建立主題常數，逐步替換 hardcode

- 試過：一次把全部 hardcode 都替換
- 為什麼放棄：風險過高，難以一次驗證
- 最終改採：先替換最常見的顏色，逐步擴展

---

## 7. PR 依賴關係

- **PR69 可獨立執行**：不需要依賴其他 PR
- **建議在 PR67（Lazy Load）之後執行**：避免與 Lazy Load 改動衝突

---

## 8. 隱性 BUG 檢查清單

- [ ] 檢查是否有顏色依賴特定上下文
- [ ] 檢查 Dark mode 對比度是否足夠
- [ ] 檢查主題切換時是否有閃爍
- [ ] 檢查 disabled 狀態的顏色是否正確

---

## 9. 預期產出

- Phase 1 完成清單
- 新增 `app/ui/theme.py`
- 新增 ThemeManager 類別
- 更新的 view 檔案
- PR 文件：`docs/pr/2026-03-13_PR69_theme_establishment.md`
