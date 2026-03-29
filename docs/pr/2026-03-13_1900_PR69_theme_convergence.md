# PR69 設計稿：主題系統收斂

> 版本：v1.0  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
統一 PR59 後的主題樣式，收斂各 view 內的主題 hardcode，建立主題常數統一管理。

### 動機
- PR59 上了主題樣式，但可能仍有 hardcode
- 各 view 的主題定義可能不一致
- 需要統一的主題管理機制

---

## 2. 現狀分析

### 2.1 PR59 主題系統
- 主題配置應該在 config 或共用常數中定義
- 目前可能仍有 view 內的 hardcode 顏色

### 2.2 需檢視的項目
1. 各 view 內的顏色 hardcode（如 `ft.Colors.XXX`）
2. 主題切換時的行為
3. Dark/Light mode 的一致性

### 2.3 驗證工具
```bash
# 找 hardcode 顏色
findstr /s "ft.Colors\." app\views\*.py
```

---

## 3. 預期改動

### 3.1 Phase 0：盤點主題 Hardcode

**執行指令**：
```powershell
# 找 hardcode 的顏色定義
findstr /s /r "ft\.Colors\." app\views\*.py
findstr /s /r "#[0-9A-Fa-f]{6}" app\views\*.py
```

### 3.2 建立主題常數

#### 3.2.1 新增 `app/ui/theme.py`
```python
"""主題常數統一管理。"""

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

# ...
```

#### 3.2.2 更新 `app/ui/__init__.py`
- 匯出主題常數

### 3.3 收斂 View 的主題使用

**原則**：
- 使用主題常數而非 hardcode
- 保持向後相容（fallback 到預設值）

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| 主題改變導致 UI 異常 | 中 | 先在測試環境驗證 |
| 漏掉某個 hardcode | 低 | 逐步替換，驗證後再下一個 |
| Dark/Light mode 不一致 | 中 | 兩種模式都要測試 |

---

## 5. Validation checklist

- [ ] Phase 0：盤點所有主題 hardcode
- [ ] 建立 `app/ui/theme.py` 主題常數
- [ ] 更新至少 3 個 view 使用主題常數
- [ ] 測試 Light mode 正常
- [ ] 測試 Dark mode 正常
- [ ] `uv run pytest -q` - 確認測試通過

---

## 6. Rejected approaches

- 試過：不动现有 hardcode，保持現狀
- 為什麼放棄：長期維護困難，主題無法統一管理
- 最終改採：建立主題常數，逐步替換 hardcode

- 試過：一次把全部 hardcode 都替換
- 為什麼放棄：風險過高，難以一次驗證
- 最終改採：先替換最常見的顏色，逐步擴展

---

## 7. 隱性 BUG 檢查清單

- [ ] 檢查是否有顏色依賴特定上下文
- [ ] 檢查 Dark mode 對比度是否足夠
- [ ] 檢查主題切換時是否有閃爍
- [ ] 檢查 disabled 狀態的顏色是否正確

---

## 8. 預期產出

- Phase 1 完成清單
- 新增 `app/ui/theme.py`
- 更新的 view 檔案
- PR 文件：`docs/pr/2026-03-13_PR69_theme_convergence.md`
