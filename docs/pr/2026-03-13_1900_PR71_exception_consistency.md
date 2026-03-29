# PR71 設計稿：Exception 使用一致性評估

> 版本：v2.0（重新設計版）  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
評估並確保全專案的 exception 處理一致性。

### 動機
- **經過驗證發現**：`translation_tool/utils/exceptions.py` **已經存在完整的 exception hierarchy**
- 設計稿原本說要「建立」，實際上已經有完整實作
- **重新定位**：從「建立」改為「評估與推廣」

---

## 2. 現狀分析

### 2.1 現有 Exception Hierarchy
`translation_tool/utils/exceptions.py` 已包含：
- `TranslationError` - 翻譯錯誤基底類別
- `CacheError` - Cache 相關錯誤
- `APIError` - API 相關錯誤
- `RateLimitError` - 速率限制錯誤
- `OverloadError` - 過載錯誤
- `FileFormatError` - 檔案格式錯誤
- `@handle_translation_errors` decorator

### 2.2 使用統計
| Exception | 使用次數 |
|-----------|----------|
| TranslationError | 2 |
| APIError | 2 |
| FileFormatError | 2 |
| CacheError | 已存在 |
| RateLimitError | 已存在 |
| OverloadError | 已存在 |

### 2.3 需檢視的項目
1. 新 code 是否使用現有 exception
2. 錯誤訊息格式是否一致
3. except 區塊的處理方式
4. 是否有 bare except 或 except: pass

---

## 3. 預期改動

### 3.1 Phase 0：評估現狀

**執行指令**：
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

### 3.2 建立使用規範

#### 3.2.1 檢查新 code 是否使用現有 exception
- 新增 code 應使用 `translation_tool.utils.exceptions.*`
- 不鼓勵直接 raise RuntimeError, ValueError

#### 3.2.2 統一錯誤訊息格式
原則：
- 格式：`{Action} failed: {reason}. Suggestion: {suggestion}`
- 包含 context 資訊
- 提供建議而非只描述問題

### 3.3 識別問題區域

#### 3.3.1 找 bare except
```bash
findstr /s "except:" app\*.py translation_tool\*.py
```

#### 3.3.2 找 except: pass
```bash
findstr /s /n "except.*pass" app\*.py translation_tool\*.py
```

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| 改動現有 exception 影響 caller | 🟠 中 | 保持向後相容 |
| 遺漏某個 error case | 🟢 低 | 全面測試覆蓋 |
| 過度規範降低靈活性 | 🟢 低 | 只針對新 code 規範 |

---

## 5. Validation checklist

- [ ] Phase 0：分析現有 exception 使用
- [ ] 確認 `translation_tool/utils/exceptions.py` 完整性
- [ ] 檢查是否有 bare except 或 except: pass
- [ ] 建立使用規範文件
- [ ] `uv run pytest -q` - 確認測試通過

---

## 6. Rejected approaches

- 試過：建立新的 exception hierarchy
- 為什麼放棄：**已經存在完整實作**
- 最終改採：評估現有使用，推廣新規範

- 試過：強迫所有現有 code 改用新 exception
- 為什麼放棄：風險過高，可能破壞現有功能
- 最終改採：只針對新 code 規範，舊 code 自願採用

---

## 7. PR 依賴關係

- **PR71 可獨立執行**：不需要依賴其他 PR

---

## 8. 隱性 BUG 檢查清單

- [ ] 檢查是否有 except Exception: pass 的情況
- [ ] 檢查是否有 exception 被 silently swallow
- [ ] 檢查錯誤訊息是否包含敏感資訊
- [ ] 檢查是否有 recursive error handling

---

## 9. 預期產出

- Phase 1 完成清單
- Exception 使用評估報告
- 使用規範文件
- PR 文件：`docs/pr/2026-03-13_PR71_exception_consistency.md`
