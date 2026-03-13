# PR62 設計稿：測試覆蓋率健檢與修復

> 版本：v1.0  
> 適用：Minecraft_translator_flet v0.6.0+  
> 編寫日期：2026-03-13

---

## 1. 目標與動機

### 目標
確認 PR61 (cache import canonicalize) 之後沒有 guard test 漏掉，並補齊測試覆蓋缺口，確保重構的安全性。

### 動機
- PR61 修改了 cache 相關模組的 import canonicalize
- 需要確認所有 guard test 仍然通過
- 補齊低覆蓋率的模組測試

---

## 2. 現狀分析

### 測試數量
- 當前測試總數：**171 個**（2026-03-13 驗證通過）
- README.md 目前仍顯示「40 passed」（需在 PR65 更新為 171）

### PR61 改動範圍
- `translation_tool/utils/cache_store.py` - 新增 state holder
- `app/views/cache_manager/` - import canonicalize
- 多個測試檔案的 import 調整

### 潛在風險點
- PR61 改動可能影響 cache 相關測試
- 部分模組測試覆蓋率可能偏低

---

## 3. 預期改動

### 3.1 確認 PR61 後的測試狀態

**執行指令**：
```bash
uv run pytest -q
```

**預期結果**：所有測試通過

### 3.2 確認 cache 相關測試

**執行指令**：
```bash
uv run pytest -k cache -v
```

**預期結果**：所有 cache 相關測試通過

### 3.3 檢查未使用的 import

**執行指令**：
```bash
ruff check --select=F401 .
```

**預期結果**：清理 F401 (unused-import) 警告

### 3.4 補齊測試（視情況）

若發現測試缺口，視情況補齊。預估可能需要補測試的模組：
- `jar_processor.py` - JAR 處理
- `lang_merge_content.py` - 合併邏輯

---

## 4. 風險評估

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| PR61 改動破壞現有測試 | 低 | 先跑測試確認狀態 |
| 補測試引發新問題 | 低 | 小範圍測試，先驗證再擴展 |

---

## 5. Validation checklist

- [ ] `uv run pytest -q` - 確認所有測試通過
- [ ] `uv run pytest -k cache -v` - 確認 cache 相關測試通過
- [ ] `python -c "import subprocess; r=subprocess.run(['uv','run','pytest','--co','-q'], capture_output=True, text=True); print(len([l for l in r.stdout.splitlines() if l.strip()]))"` - 確認測試數量為 171
- [ ] `ruff check --select=F401 .` - 檢查未使用的 import
- [ ] 記錄覆蓋率缺口（若有）

---

## 6. Rejected approaches

- 試過：跳過測試直接進入下一個 PR
- 為什麼放棄：違反 ITERATION_SOP 安全網原則，PR61 是重構類 PR，必須確認測試完整性
- 最終改採：先執行測試確認狀態，再進入後續 PR

---

## 7. 刪除/移除/替換說明

本 PR 預設不刪除任何功能。若有發現未使用的 import，視情況清理。

---

## 8. 預期產出

- Phase 1 完成清單（測試執行結果）
- 補齊的測試檔案（若有）
- PR 文件：`docs/pr/2026-03-13_PR62_test_coverage_health_check.md`
