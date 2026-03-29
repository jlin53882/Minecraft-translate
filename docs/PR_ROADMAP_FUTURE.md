# Minecraft_translator_flet 未來 PR 規劃

> 適用版本：v0.6.0 (main branch)  
> 編寫日期：2026-03-13  
> 狀態：規劃中

---

## 📋 當前狀態摘要

| 項目 | 狀態 |
|------|------|
| 最新 commit | `da78143` (PR61 merge) |
| 最新 release | v0.6.0 |
| app/services.py | 已收斂為 QC façade |
| cache canonicalize | PR61 完成 |
| CI | uv sync + pytest + ruff |

---

## 🎯 PR 序列規劃（不含 QC 重構）

### Phase 1：測試與安全網

#### PR62：測試覆蓋率健檢與修復

**目標**：確認 PR61 後沒有 guard test 漏掉，補齊覆蓋缺口

**主要內容**：
- 執行 `pytest -q --cov=translation_tool --cov=app --cov-report=term-missing`
- 修補低覆蓋率的模組（預期：jar_processor、lang_merge、kubejs_translator）
- 確認 PR61 改動後的 import canonicalize 沒有破壞任何 guard test
- 執行 `pytest -k cache` 確認 cache 相關測試全綠

**驗證清單**：
```bash
pytest -q
pytest -q --cov=translation_tool --cov=app
pytest -k cache -v
ruff check .
```

**風險**：低（純測試補丁）

---

#### PR63：測試重構 - 消滅重複 fixture

**目標**：統一測試 fixtures，降低維護成本

**主要內容**：
- 審視 `tests/conftest.py` 與各 test file 內的重複 fixture
- 提取共用 fixtures 到 conftest.py
- 確保 `test_cache_*.py` 系列使用統一的 mock pattern

**檔案變動**：
- `tests/conftest.py` - 擴充共用 fixtures
- 可能的話：重構部分 test file 使用 shared fixtures

**驗證清單**：
```bash
pytest -q
# 確認測試數量不減少
pytest --co -q | wc -l
```

**風險**：中（改動測試需謹慎）

---

### Phase 2：文檔補完

#### PR64：Docstring 補完計畫

**目標**：依據 `docs/DOCSTRING_SPEC.md` 補齊核心模組的 docstring

**主要內容**：
- 補齊 `translation_tool/core/` 核心翻譯模組的 docstring
- 補齊 `app/views/` 重要 view 函數的 docstring
- 執行 `ruff check --select=D` 檢查缺失

**優先順序**（依賴度排序）：
1. `lm_translator.py` - LM 翻譯核心
2. `lang_merger.py` - 合併核心
3. `jar_processor.py` - JAR 處理核心
4. 各 view 的主要處理函數

**驗證清單**：
```bash
ruff check --select=D .
# 目標：D 開頭警告數量減少 50%+
```

**風險**：低（僅文件變動）

---

#### PR65：README 與架構文檔更新

**目標**：更新專案文件反映當前架構

**主要內容**：
- 更新 `README.md` 反映 v0.6.0 架構
- 補齊 `docs/` 下的架構圖或 API 文件
- 確認 `CHANGELOG.md` 與 release notes 一致

**驗證清單**：
```bash
# 確認文件語法正確
markdownlint docs/*.md
```

**風險**：低

---

### Phase 3：效能優化

#### PR66：Cache 效能優化

**目標**：利用 PR61 canonicalize 後的 cache 結構優化命中率

**主要內容**：
- 分析 cache 命中率瓶頸
- 實作 cache key 優化（減少 collision）
- 考慮加入 cache warm-up 機制
- 優化 `cache_store.py` 的讀寫路徑

**驗證清單**：
```bash
# 效能基準測試（需建立 benchmark）
pytest -q
# cache 相關功能正常
pytest -k cache -v
```

**風險**：中（改動 cache 邏輯需全面測試）

---

#### PR67：Lazy Load 優化

**目標**：減少啟動時間，按需載入模組

**主要內容**：
- 分析 `app/views/` 的 import 依賴圖
- 將非必要的 import 改為 lazy import
- 特別是大型 view：`cache_view.py` (146KB)、`rules_view.py` (25KB)

**預期收益**：
- 縮短 cold start 時間
- 減少記憶體佔用

**驗證清單**：
```bash
# 確認 import 正常
python -c "from app.views import *"
pytest -q
```

**風險**：中（lazy import 可能觸發隱藏依賴問題）

---

### Phase 4：UI 重構

#### PR68：UI Component 抽取

**目標**：減少重複的 UI 程式碼

**主要內容**：
- 審視 `app/ui/` 下的共用元件
- 抽取重複的 button、input、dialog pattern
- 建立 `app/ui/components.py` 或類似模組

**預期收益**：
- 降低 view 檔案大小
- 提高 UI 一致性

**驗證清單**：
```bash
# UI 功能測試
pytest -k view -v
# Flet UI 手動測試
```

**風險**：中（UI 改動需手動驗證）

---

#### PR69：主題系統收斂

**目標**：統一 PR59 後的主題樣式

**主要內容**：
- 收斂各 view 內的主題 hardcode
- 建立主題常數統一管理
- 確認 dark/light mode 一致性

**驗證清單**：
```bash
pytest -q
# 手動切換主題測試
```

**風險**：低

---

### Phase 5：清理與技術債務

#### PR70：移除廢棄程式碼

**目標**：清理不再使用的 code

**主要內容**：
- 檢查是否有未使用的 import/module
- 清理 `.tmp/` 或 `backups/` 中的舊檔案（若有）
- 執行 `ruff check --select=F401 .` 檢查未使用的 import
- 檢查是否有 dead code

**驗證清單**：
```bash
ruff check --select=F401,F841 .
git status
```

**風險**：低（需確認真的沒用到）

---

#### PR71：Error Handling 統一

**目標**：統一全專案的 exception 處理

**主要內容**：
- 審視各模組的 error handling pattern
- 建立統一的 exception hierarchy
- 確保 error message 一致

**驗證清單**：
```bash
pytest -q
# 錯誤情境測試
```

**風險**：中（改動 exception 可能影響 caller）

---

## 🚀 新功能候選（Phase 6+）

> 視團隊需求決定是否實作

### 功能候選 1：翻譯歷史記錄
- 儲存每次翻譯的 audit trail
- 可回溯、可匯出

### 功能候選 2：批次匯入優化
- 支援多檔案批次處理
- 進度條顯示

### 功能候選 3：Plugin 系統擴展
- 讓翻譯規則可外掛
- 社群貢獻更容易

### 功能候選 4：i18n UI
- 多語言界面
- 英文/繁中/簡中

---

## 📊 PR 執行順序建議

```
Phase 1 (安全網)
├── PR62 測試覆蓋率健檢 ← 立即執行
└── PR63 測試重構

Phase 2 (文檔)
├── PR64 Docstring 補完
└── PR65 README 更新

Phase 3 (效能)
├── PR66 Cache 優化
└── PR67 Lazy Load

Phase 4 (UI)
├── PR68 Component 抽取
└── PR69 主題收斂

Phase 5 (清理)
├── PR70 廢棄程式碼
└── PR71 Error Handling
```

**預估時程**：每個 PR 1-2 天，共約 10 個 PR（2-3 週）

---

## ⚠️ 已知技術債務

| 項目 | 說明 | 優先度 |
|------|------|--------|
| `app/views/qc_view.py` | 預計重構 | 已暫緩 |
| `cache_view.py` (146KB) | 太大，需拆分 | 中 |
| `lang_merge_content_copy.py` | 16KB，可能有重疊邏輯 | 中 |
| jar_processor 模組 | 測試覆蓋率低 | 高 |
| LM API error handling | 需更強健 | 中 |

---

## 🔗 相關檔案

- `ITERATION_SOP.md` - 疊代規範
- `docs/DOCSTRING_SPEC.md` - Docstring 規範
- `docs/GH_WORKFLOW.md` - CI/CD 工作流
- `RELEASE_STRATEGY.md` - Release 策略
