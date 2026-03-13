# PR68 設計稿：UI Component 抽取

> 版本：v1.0  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
抽取重複的 UI 程式碼到共用元件，降低維護成本與檔案大小。

### 動機
- 現有 `app/ui/components.py` 已有一些共用元件
- 多個 view 仍有重複的按鈕定義、對話框等
- 統一元件可提高 UI 一致性

---

## 2. 現狀分析

### 2.1 現有共用元件
`app/ui/components.py` 已包含：
- `section_header()` - 區塊標題列
- `styled_card()` - 統一卡片外觀
- `primary_button()` - 主動作按鈕
- `secondary_button()` - 次要按鈕

### 2.2 重複的 UI Pattern
| View | ElevatedButton 數量 |
|------|---------------------|
| cache_view.py | 5 |
| icon_preview_view.py | 4 |
| extractor_view.py | 3 |
| qc_view.py | 3 |
| lookup_view.py | 2 |
| bundler_view.py | 1 |

### 2.3 可抽取的 Pattern
1. **確認對話框** - 多個 view 有相同的確認邏輯
2. **訊息提示** - Toast / SnackBar
3. **載入指示器** - 進度條
4. **列表卡片** - 重複的列表項目樣式

---

## 3. 預期改動

### 3.1 Phase 0：盤點重複 UI Code

**執行指令**：
```python
# 統計各 view 的重複 pattern
python -c "
import pathlib
views = pathlib.Path('app/views').glob('*.py')
for v in views:
    if v.name.startswith('__'): continue
    content = v.read_text(encoding='utf-8')
    # 統計 ft.ElevatedButton, ft.AlertDialog 等
    for pat in ['ElevatedButton', 'AlertDialog', 'SnackBar']:
        count = content.count(pat)
        if count > 0:
            print(f'{v.name}: {count} {pat}')
"
```

### 3.2 新增共用元件

#### 3.2.1 確認對話框
```python
def confirm_dialog(
    title: str,
    content: str,
    on_confirm,
    on_cancel=None,
) -> ft.AlertDialog:
    """統一的確認對話框。"""
```

#### 3.2.2 訊息提示
```python
def show_message(page, message, type="info"):
    """統一的訊息顯示（info/success/error）。"""
```

#### 3.2.3 載入指示器
```python
def loading_overlay(show: bool):
    """統一的載入中遮罩。"""
```

### 3.3 更新 View 使用共用元件

**預期修改**：
- `app/views/cache_view.py` - 使用 confirm_dialog
- `app/views/extractor_view.py` - 使用 confirm_dialog
- 其他 view 依此類推

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| UI 行為改變 | 中 | 保持原有外觀，只做重構 |
| 測試失敗 | 低 | UI 改動不影響單元測試 |
| Flet 版本相容 | 低 | 使用穩定的公共 API |

---

## 5. Validation checklist

- [ ] Phase 0：盤點重複 UI pattern
- [ ] 新增共用元件到 `app/ui/components.py`
- [ ] 更新至少 3 個 view 使用共用元件
- [ ] `uv run pytest -q` - 確認測試通過
- [ ] 手動測試 UI 正常運作

---

## 6. Rejected approaches

- 試過：不動現有 components.py，各自保留重複 code
- 為什麼放棄：長期維護成本高
- 最終改採：抽取重複 pattern 到共用元件

- 試過：一次把所有 UI code 都抽取
- 為什麼放棄：風險過高，難以一次驗證
- 最終改採：先抽取最常見的 pattern（對話框、訊息）

---

## 7. 隱性 BUG 檢查清單

- [ ] 檢查是否有 view 依賴特定的對話框行為
- [ ] 檢查 SnackBar/AlertDialog 的 on_dismiss 是否正確傳遞
- [ ] 檢查 Dark/Light mode 下樣式是否正確
- [ ] 檢查按鈕權限/disabled 狀態是否保留

---

## 8. 預期產出

- Phase 1 完成清單
- 更新的 `app/ui/components.py`
- 更新的 view 檔案
- PR 文件：`docs/pr/2026-03-13_PR68_ui_component_extraction.md`
