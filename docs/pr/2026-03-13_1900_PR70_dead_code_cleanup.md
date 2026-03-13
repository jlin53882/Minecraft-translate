# PR70 設計稿：移除廢棄程式碼

> 版本：v1.0  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
清理不再使用的程式碼，降低維護成本與理解成本。

### 動機
- 經過 PR40-61 大重構，可能有廢棄的 import/module
- 清理 dead code 可減少混淆
- 提高程式碼可讀性

---

## 2. 現狀分析

### 2.1 可檢查的項目
1. **未使用的 import** - `ruff check --select=F401`（約 20 個）
2. **未使用的變數** - `ruff check --select=F841`
3. **無法達到的程式碼** - unreachable code
4. **過時的註解** - TODO/FIXME/NOTE 可能已過時

### 2.2 當前檢查結果
| 檢查項 | 狀態 |
|--------|------|
| F401 (unused import) | 約 20 個（需逐一確認是否為故意保留的 export）|
| F841 (unused variable) | 0 個 |
| C901 (complexity) | 待檢查 |

### 2.3 需特別注意
- **並非所有 F401 都是問題**：有些是故意保留的 export（如 `__all__`）
- 需要逐一確認：`# noqa: F401` 是否是有意義的 export

### 2.3 需清理的目錄
- `.tmp/` - 臨時檔案
- `backups/` - 舊備份（若有）
- 可能的 worktree noise

---

## 3. 預期改動

### 3.1 清理未使用的 Import

**執行指令**：
```bash
ruff check --select=F401 .
```

**處理方式**：
- 確認每個 F401 確實未使用
- 移除或加上 `# noqa: F401`（若是有意義的 export）

### 3.2 清理臨時檔案

**執行指令**：
```powershell
# 檢查 .tmp/ 目錄
dir .tmp

# 檢查 backups/ 目錄
dir backups
```

**處理方式**：
- 清理空的或過時的臨時檔案
- 保留有價值的備份

### 3.3 檢查 Dead Code

**執行指令**：
```bash
ruff check --select=F704 .  # unreachable code
```

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| 誤刪正在使用的 code | 高 | 每次刪除前用 grep 確認無引用 |
| 刪除有價值的註解 | 低 | 保留有價值的歷史記錄 |
| 破壞 git history | 低 | 只刪除檔案，不改變歷史 |

---

## 5. Validation checklist

- [ ] `ruff check --select=F401 .` - 清理未使用的 import
- [ ] `ruff check --select=F841 .` - 清理未使用的變數
- [ ] 檢查 `.tmp/` 和 `backups/` 目錄
- [ ] `uv run pytest -q` - 確認測試通過
- [ ] `git status` - 確認改動範圍

---

## 6. Rejected approaches

- 試過：使用自動工具一次清理全部
- 為什麼放棄：可能誤刪有價值的程式碼
- 最終改採：手動審視每個警告，確認後再處理

- 試過：只清理 import，其他不動
- 為什麼放棄：維護成本仍然高
- 最終改採：全面檢查，包括臨時檔案

---

## 7. 隱性 BUG 檢查清單

- [ ] 檢查是否有 `# noqa: F401` 是故意保留的 export
- [ ] 檢查是否有未使用的 function 是預留 API
- [ ] 檢查測試是否依賴某些「未使用」的 module
- [ ] 檢查 config 是否有必須保留的 fallback

---

## 8. 預期產出

- Phase 1 完成清單
- 清理後的程式碼
- PR 文件：`docs/pr/2026-03-13_PR70_dead_code_cleanup.md`
