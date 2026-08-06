# PR 工作流模板

> 適用版本：v0.6.0+  
> 更新日期：2026-08-06

---

## 發布格式

以後所有 PR 執行彙報使用以下格式：

```markdown
🚀 **[任務主題]**
對應 PR： [PR 編號及連結]

## 1. 執行摘要 (Executive Summary)
- 目的： [簡述本次任務目標]
- 狀態： [✅ 完成 / ⏳ 進行中 / ⚠️ 待辦]

## 2. 實作變更 (Implementation Changes)
- 核心變更： [簡述修改內容]
- 影響範圍： [涉及的模組或檔案]

## 3. 測試驗證 (Verification & Quality)
| 測試項目 | 狀態 | 驗證數據/結果 |
|----------|------|----------------|
| Full Regression | [✅/❌] | [例如：1905 passed] |
| Targeted Tests | [✅/❌] | [例如：42 passed] |
| Static Analysis | [⚠️/✅] | [例如：F401 警告數] |

## 4. 健檢結論 (Conclusion & Health Check)
- 穩定性： [說明改動是否影響現有邏輯]
- 清理清單： [列出待處理但非本次範圍的雜訊]

## 5. 下一步規劃 (Next Steps)
[例如：合併 PR61 / 進入 PR70 清理階段]
```

---

## 執行範例：PR62 測試覆蓋率健檢

🚀 **PR62 測試覆蓋率健檢**
對應 PR： PR62（測試覆蓋率健檢）

### 1. 執行摘要
- 目的：確認 PR61 後沒有 guard test 漏掉
- 狀態： ✅ 完成

### 2. 實作變更
- 核心變更：僅執行驗證，未修改程式碼
- 影響範圍：測試套件

### 3. 測試驗證
| 測試項目 | 狀態 | 驗證數據 |
|----------|------|----------|
| Full Regression | ✅ | 1905 passed |
| Targeted Tests (cache) | ✅ | 42 passed |
| Static Analysis (F401) | ⚠️ | 20 個警告（PR70 處理）|

### 4. 健檢結論
- 穩定性： PR61 改動未破壞任何測試
- 清理清單： F401 警告（非 PR62 範圍，PR70 處理）

### 5. 下一步
- PR62 完成，可進入 PR63（依賴 PR62）或 PR66（依賴 PR62）

---

## 1. PR 流程圖

```
┌─────────────────────────────────────────────────────────────┐
│                      設計階段                               │
├─────────────────────────────────────────────────────────────┤
│  1. Phase 0 現狀分析                                       │
│     □ 目錄結構檢查（dir app/, dir tests/）                  │
│     □ 關鍵檔案讀取                                          │
│     □ 工具可用性驗證（ruff, pytest）                        │
│     □ 數據統計                                              │
│     □ 交叉比對記憶與現狀                                    │
│                                                             │
│  2. 設計稿撰寫                                             │
│     □ 目標與動機                                           │
│     □ 現狀分析                                             │
│     □ 預期改動                                             │
│     □ 風險評估                                             │
│     □ Validation checklist                                  │
│     □ Rejected approaches                                  │
│     □ 隱性 BUG 檢查清單                                    │
│                                                             │
│  3. 交付前 self-check                                      │
│     □ 設計稿完成後自己跑一次驗證                             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                      驗證階段                               │
├─────────────────────────────────────────────────────────────┤
│  4. 外部驗證（家豪 / OpenAI）                               │
│     □ 確認設計可行                                          │
│     □ 檢查隱性問題                                         │
│     □ 修正設計                                              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                      執行階段                               │
├─────────────────────────────────────────────────────────────┤
│  5. Phase 1 實作                                           │
│     □ 依設計稿逐項執行                                      │
│     □ 先建立可回退備份                                       │
│     □ 列出 Phase 1 完成清單                                  │
│                                                             │
│  6. Phase 2 驗證 + PR                                      │
│     □ 執行 Validation checklist                             │
│     □ 貼上真實輸出                                          │
│     □ 撰寫 PR 文件                                          │
│     □ 推送到 GitHub                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 設計稿模板

參考：`ITERATION_SOP.md`

### 必要章節
1. 目標與動機
2. 現狀分析
3. 預期改動
4. 風險評估
5. **Validation checklist**（必填）
6. **Rejected approaches**（必填）
7. 隱性 BUG 檢查清單
8. PR 依賴關係

---

## 3. 設計前必做：現狀分析清單

```
□ 目錄結構檢查（dir app/, dir tests/）
□ 關鍵檔案讀取（conftest.py, exceptions.py 等）
□ 工具可用性驗證
□ 數據統計
□ 交叉比對記憶與現狀
```

---

## 4. PR 命名規範

採用 Conventional Commits 風格，branch / commit / PR title 統一使用 `type(scope): 描述`：

```
分支：   type/scope-description
Commit： type(scope): 描述
PR 標題：type(scope): 描述
```

type 常用值：`feat` / `fix` / `refactor` / `docs` / `perf` / `test` / `chore` / `ci`

範例：
- 分支：`docs/extractor-architecture`
- Commit：`docs: MERGE 補合併決策邏輯（人工 zh_tw 保護 / 優先序）`
- PR 標題：`docs: 新增 Extractor View 架構文件並修正過時 docs`

> 註：過去的 PR 設計稿存放於 `docs/pr/`，已於 2026-08 清理刪除；歷史設計決策以 git history 為準（不再使用 `YYYY-MM-DD_HHmm_PR<數字>_<主題>.md` 命名）。

---

## 5. 執行順序（歷史，已全部完成）

以下為 2026-03 的早期批次規劃，已全數完成；現行開發以 GitHub Issue / 需求為準，不再以固定批次編號（PR<數字>）作為命名依據。
| PR | 主題 | 依賴 |
|----|------|------|
| PR62 | 測試覆蓋率健檢 | 獨立 |
| PR63 | 測試基礎設施建立 | PR62 |
| PR64 | Docstring 補完 | 獨立 |
| PR65 | README 更新 | 獨立 |
| PR66 | Cache 效能優化 | PR62 |

### 第二批（PR67-71）
| PR | 主題 |
|----|------|
| PR67 | Lazy Load 優化 |
| PR68 | UI Component 抽取 |
| PR69 | 主題系統建立 |
| PR70 | 移除廢棄程式碼 |
| PR71 | Exception 使用一致性評估 |

---

## 6. GitHub 操作（Windows）

參考：`docs/GH_WORKFLOW.md`

### 標準 PR 流程
```powershell
# 1. 建立分支（type/ 前綴，見第 4 節）
git checkout -b docs/extractor-architecture

# 2. 開發與 commit（Conventional Commits）
git add .
git commit -m "docs: 補齊 EXTRACTOR 邏輯細節"

# 3. 推送
git push -u origin docs/extractor-architecture

# 4. 建立 PR（使用 gh CLI，完整路徑與細節見 GH_WORKFLOW.md）
& "C:\Program Files\GitHub CLI\gh.exe" pr create `
  --base main `
  --head docs/extractor-architecture `
  --title "docs: 新增 Extractor View 架構文件" `
  --body-file "$env:TEMP\pr-body.md"
```

### Release 流程
```powershell
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file release_notes_vX.Y.Z.md
```

---

## 7. 相關檔案

| 檔案 | 用途 |
|------|------|
| `ITERATION_SOP.md` | 疊代規範 |
| `docs/GH_WORKFLOW.md` | GitHub 操作流程 |
| `docs/RELEASE_WORKFLOW.md` | Release 流程 |
| `docs/DOCSTRING_SPEC.md` | Docstring 規範 |

---

## 8. 注意事項

1. **設計前必做現狀分析**：禁止只依賴記憶
2. **PR 文件用 .md 保存**：設計稿、設計討論、PR 文件都要
3. **每個 PR 要有 Validation checklist**：不得捏造輸出
4. **Rejected approaches 必填**：至少一條
5. **備份規則**：備份檔放 `backups/<pr-slug-timestamp>/`
