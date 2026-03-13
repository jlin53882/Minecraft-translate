# PR64 設計稿：Docstring 補完計畫

> 版本：v1.0  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
依據 `docs/DOCSTRING_SPEC.md` 補齊核心模組的 docstring，提高程式可維護性。

### 動機
- v0.6.0 release 後，核心模組需要完整的 API 文件
- Docstring 有助於：
  - 新成員理解程式碼
  - IDE 自動完成
  - 未來的重構參考

---

## 2. 現狀分析

### DOCSTRING_SPEC.md 要求
- 需參考 `docs/DOCSTRING_SPEC.md` 的格式規範

### 當前缺失
- 執行 `ruff check --select=D .` 可檢視缺失的 docstring

### 優先順序（依賴度排序）
1. `translation_tool/core/lm_translator.py` - LM 翻譯核心（~24KB）
2. `translation_tool/core/lang_merger.py` - 合併核心（~10KB）
3. `translation_tool/core/jar_processor.py` - JAR 處理核心（~5KB）
4. 各 view 的主要處理函數

---

## 3. 預期改動

### 3.1 檢視當前 Docstring 缺口

**執行指令**：
```bash
ruff check --select=D .
```

**預期結果**：列出所有缺少 docstring 的模組/函數

### 3.2 補齊核心模組 Docstring

#### 3.2.1 lm_translator.py
- 類別：`LMTranslator`, `LMTranslatorSession`
- 主要方法：`_translate_single`, `_translate_batch`, `translate`

#### 3.2.2 lang_merger.py
- 類別：`LangMerger`, `MergeContext`
- 主要方法：`merge_files`, `merge_content`, `resolve_conflict`

#### 3.2.3 jar_processor.py
- 類別：`JarProcessor`, `JarDiscovery`
- 主要方法：`process_jar`, `extract_lang_files`

### 3.3 補齊 View Docstring

優先補齊的 view（按大小/複雜度）：
- `app/views/cache_view.py` (146KB) - 重要
- `app/views/rules_view.py` (25KB) - 重要
- `app/views/config_view.py` (25KB) - 重要
- 其他 view 的主要處理函數

### 3.4 格式規範

依據 `docs/DOCSTRING_SPEC.md`：
- 使用 Google style 或 NumPy style
- 包含：Args, Returns, Raises, Examples

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| 改錯現有 docstring 語法 | 低 | 保持原有語法，只補充缺失 |
| 影響程式行為 | 無 | Docstring 不影響 runtime |
| 格式不一致 | 低 | 依據 DOCSTRING_SPEC.md 統一 |

---

## 5. Validation checklist

- [ ] `ruff check --select=D .` - 列出缺失 docstring
- [ ] 補齊 `lm_translator.py` docstring
- [ ] 補齊 `lang_merger.py` docstring
- [ ] 補齊 `jar_processor.py` docstring
- [ ] 補齊主要 view 的 docstring
- [ ] `ruff check --select=D .` - 確認 D 開頭警告減少 50%+

---

## 6. Rejected approaches

- 試過：一次補完所有模組
- 為什麼放棄：風險過高，萬一格式錯誤難以排查
- 最終改採：分階段補齊，先核心再 views

- 試過：使用自動生成工具補 docstring
- 為什麼放棄：自動生成往往質量低，不符合 DOCSTRING_SPEC.md 規範
- 最終改採：手動補齊，確保質量

---

## 7. 刪除/移除/替換說明

本 PR 不刪除任何功能。純粹補充文件。

---

## 8. 預期產出

- Phase 1 完成清單（補齊的模組清單）
- 更新的模組檔案（帶完整 docstring）
- PR 文件：`docs/pr/2026-03-13_PR64_docstring_completion.md`

---

## 9. 里程碑

| 階段 | 內容 |
|------|------|
| Phase 1 | 執行 `ruff check --select=D .` 分析缺口 |
| Phase 2 | 補齊核心翻譯模組 (lm_translator, lang_merger, jar_processor) |
| Phase 3 | 補齊主要 views |
| Phase 4 | 最終驗證 |

---

## 10. 備註

- 本 PR 為純文件變動，不影響 runtime
- 預估時程：1-2 天
- 建議分配：每人負責 1-2 個模組
