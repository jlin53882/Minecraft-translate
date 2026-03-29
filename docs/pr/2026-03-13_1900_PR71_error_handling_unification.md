# PR71 設計稿：Error Handling 統一

> 版本：v1.0  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
統一全專案的 exception 處理與錯誤訊息，提升錯誤可讀性與可追蹤性。

### 動機
- 現有多种不同的 exception 定義
- 錯誤訊息格式不一致
- 不利於錯誤追蹤與監控

---

## 2. 現狀分析

### 2.1 當前 Exception 使用
| Exception | 使用次數 |
|-----------|----------|
| FileNotFoundError | 19 |
| RuntimeError | 9 |
| ValueError | 5 |
| TypeError | 3 |
| KeyError | 2 |
| 自訂 Exception | 2+ |

### 2.2 需檢視的項目
1. 各 module 的 exception 定義
2. 錯誤訊息的格式
3. except 區塊的處理方式

### 2.3 驗證工具
```python
# 統計 exception 使用
python -c "
import pathlib, re
exceptions = {}
for p in pathlib.Path('translation_tool').rglob('*.py'):
    content = p.read_text(encoding='utf-8')
    raises = re.findall(r'raise\s+(\w+)', content)
    for r in raises:
        exceptions[r] = exceptions.get(r, 0) + 1
for k, v in sorted(exceptions.items(), key=lambda x: -x[1])[:15]:
    print(f'{k}: {v}')
"
```

---

## 3. 預期改動

### 3.1 建立 Exception Hierarchy

#### 3.1.1 新增 `translation_tool/utils/exceptions.py` 或擴展現有
```python
"""統一 exception 定義。"""

class TranslationError(Exception):
    """翻譯相關錯誤的基礎類別。"""

class CacheError(TranslationError):
    """Cache 相關錯誤。"""

class APIError(TranslationError):
    """API 相關錯誤。"""

class ValidationError(TranslationError):
    """驗證相關錯誤。"""
```

### 3.2 統一錯誤訊息格式

**原則**：
- 格式：`{Action} failed: {reason}. Suggestion: {suggestion}`
- 包含 context 資訊
- 提供建議而非只描述問題

### 3.3 更新現有 Exception 使用

**原則**：
- 逐步遷移，不一次全部替換
- 保持向後相容
- 新增 logging

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| 改動 exception 影響 caller | 高 | 確保所有 caller 都正確處理 |
| 遺漏某個 error case | 中 | 全面測試覆蓋 |
| Logging 過多/過少 | 低 | 根據實際需求調整 |

---

## 5. Validation checklist

- [ ] Phase 0：分析現有 exception 使用
- [ ] 建立統一的 exception hierarchy
- [ ] 更新至少 3 個 module 使用新的 exception
- [ ] 確認錯誤訊息格式一致
- [ ] `uv run pytest -q` - 確認測試通過

---

## 6. Rejected approaches

- 試過：一次把所有 exception 都替換成新的
- 為什麼放棄：風險過高，難以全面驗證
- 最終改採：先建立 hierarchy，逐步遷移

- 試過：保持現狀，不做統一
- 為什麼放棄：長期維護困難，錯誤難以追蹤
- 最終改採：建立基本的 exception hierarchy，強制新 code 使用

---

## 7. 隱性 BUG 檢查清單

- [ ] 檢查是否有 except Exception: pass 的情況
- [ ] 檢查是否有 exception 被 silently swallow
- [ ] 檢查錯誤訊息是否包含敏感資訊
- [ ] 檢查是否有 recursive error handling

---

## 8. 預期產出

- Phase 1 完成清單
- 更新的 exception 定義
- 更新的 module
- PR 文件：`docs/pr/2026-03-13_PR71_error_handling_unification.md`
