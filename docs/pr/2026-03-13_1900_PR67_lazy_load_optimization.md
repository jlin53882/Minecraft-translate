# PR67 設計稿：Lazy Load 優化

> 版本：v1.0  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
減少啟動時間與記憶體佔用，透過按需載入模組優化效能。

### 動機
- 當前啟動時所有 view 同步載入
- 大型 view（如 cache_view.py 146KB）拖慢啟動速度
- Lazy load 可顯著縮短 cold start 時間

---

## 2. 現狀分析

### 2.1 當前 Import 數量
| View | Import 數量 | 檔案大小 |
|------|-------------|----------|
| cache_view.py | 14 | 146KB |
| translation_view.py | 11 | 12KB |
| rules_view.py | 9 | 25KB |
| lm_view.py | 8 | 11KB |
| icon_preview_view.py | 8 | 16KB |
| merge_view.py | 7 | 12KB |
| bundler_view.py | 6 | 9KB |
| qc_view.py | 6 | 19KB |
| config_view.py | 6 | 25KB |
| extractor_view.py | 6 | 13KB |

### 2.2 當前 Import 機制
- `app/view_registry.py` 在頂部直接 import 所有 view：
  ```python
  from app.views.cache_view import CacheView
  from app.views.config_view import ConfigView
  # ... 所有 view 都在頂部 import
  ```
- 這意味著啟動時所有 view 都會被載入

### 2.3 潛在風險點
1. **隱性依賴**：某些 view 可能依賴全局 import 的 side effect
2. **循環依賴**：改動可能觸發循環 import 問題
3. **測試覆蓋**：lazy import 可能讓某些測試失敗

### 2.3 驗證工具
```bash
python -c "from app.views import *"
```

---

## 3. 預期改動

### 3.1 分析 Import 依賴圖

**Phase 0 盤點**：
1. 找出每個 view 的 import 依賴
2. 識別可延遲載入的模組
3. 確認沒有全局 side effect

**執行指令**：
```python
# 分析每個 view 的 import
python -c "
import pathlib
views = pathlib.Path('app/views').glob('*.py')
for v in views:
    if v.name.startswith('__'): continue
    content = v.read_text(encoding='utf-8')
    lines = [l for l in content.splitlines() if l.strip().startswith('import ') or l.strip().startswith('from ')]
    print(f'{v.name}: {len(lines)} imports')
"
```

### 3.2 實作 Lazy Import

#### 3.2.1 修改 app/view_registry.py（注意：不是 view_registry.py）
- 找到 view 實例化位置
- 將同步 import 改為 lazy import
- 保持 API 向後相容

#### 3.2.2 更新 view 的 __init__.py
- 使用 `__getattr__` 實現延遲載入

### 3.3 驗證

**執行指令**：
```bash
# 確認 import 正常
python -c "from app.views import *"
uv run pytest -q
```

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| 循環依賴 | 中 | 先在測試環境驗證，失敗則回退 |
| 隱性 side effect | 中 | 全面測試覆蓋，確認功能正常 |
| 啟動失敗 | 高 | 保留 fallback，確保不影響正常啟動 |

---

## 5. Validation checklist

- [ ] Phase 0：分析所有 view 的 import 依賴
- [ ] 識別可 lazy load 的模組清單
- [ ] `python -c "from app.views import *"` - 確認基本 import 正常
- [ ] 實作 lazy import 改動
- [ ] `uv run pytest -q` - 確認所有測試通過
- [ ] 啟動時間 benchmark（優化前 vs 優化後）

---

## 6. Rejected approaches

- 試過：直接刪除未使用的 import
- 為什麼放棄：風險過高，可能破壞依賴
- 最終改採：保持 import，只改載入時機（lazy）

- 試過：一次把全部 view 改成 lazy
- 為什麼放棄：風險過高，萬一有問題難以排查
- 最終改採：先改大型 view（cache_view.py），確認沒問題後再擴展

---

## 7. 隱性 BUG 檢查清單

- [ ] 檢查是否有 module-level 的 side effect（如啟動時自動載入配置）
- [ ] 檢查是否有 view 依賴其他 view 的 import
- [ ] 檢查測試是否依賴全局 import
- [ ] 檢查是否有循環依賴

---

## 8. 預期產出

- Phase 1 完成清單
- Lazy import 改動
- PR 文件：`docs/pr/2026-03-13_PR67_lazy_load_optimization.md`
