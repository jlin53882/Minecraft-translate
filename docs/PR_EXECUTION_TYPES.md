# PR Execution Types
# PR 執行類型 / 時間花費對照表

目的：把 PR1～PR4 的實際執行經驗整理成可重用的工作分類，讓之後規劃新 PR 時，能先判斷這顆比較像哪一型、時間會花在哪、該先準備什麼。

---

## 為什麼需要這份表

同樣叫做「做一顆 PR」，實際耗時來源可能完全不同：
- 有的時間花在**理解現狀**
- 有的時間花在**證明能刪**
- 有的時間花在**副作用與邊界收斂**
- 有的時間花在**把意圖寫清楚**

如果一開始就知道這顆 PR 是哪一型，準備方式會差很多：
- 盤點型 → 先給足分析資料
- 驗證型 → checklist 要明確寫「怎麼證明」
- 邊界型 → 先做 Phase 0，掃 import / guard test / hardcoded path
- 文件型 → 風險低，可給較大自主空間，但要要求說明品質

---

# PR1～PR4 對照表

| PR | 類型 | 最花時間的地方 | 為什麼花時間 |
|----|------|----------------|--------------|
| PR1 | 盤點型 | 建 baseline、補分析索引、修 review bundle 矛盾 | 不是改功能，是先搞清楚專案現狀與既有問題 |
| PR2 | 驗證型 | 判斷哪些東西真的能刪、清 `__pycache__` 汙染、驗證 caller | 改動本身不大，難的是證明刪除安全 |
| PR3 | 邊界型 | import-time side effect、啟動責任、wrapper 邊界 | 一不小心就會把 `import main` / `from app import services` 弄炸 |
| PR4 | 文件型 | 註解與說明品質、意圖與邊界表達 | 幾乎不改功能，時間主要花在把未來維護成本降下來 |

---

# 1. 盤點型 PR

## 定義
這類 PR 的目標不是先改功能，而是建立「重構或修正前的地圖」。

## 典型特徵
- 建索引 / 盤點模組 / 補分析報告
- 先跑 baseline test
- 確認哪些錯是 pre-existing、哪些是之後不能碰壞的
- 產出 canonical 重構順序

## PR1 為什麼屬於這類
PR1 的主要工作是：
- 建立 `.agentlens/INDEX.md`
- 產出 / 修正 `01-main-py-review.md`、`02-app-review.md`、`03-translation-tool-review.md`、`code-review.md`
- 跑 `uv run pytest` 建 baseline
- 確認 3 個既有 collection error 是重構前就存在

## 最花時間的地方
- 修分析報告彼此矛盾
- 補齊 root-level script / tests / maintenance 腳本狀態
- 建 baseline，避免之後重構時分不清是本來就壞還是新弄壞

## 開工前應準備
- `.agentlens/INDEX.md` / review bundle
- baseline test output
- 專案目錄結構與模組摘要

## 適合的 checklist 方向
- baseline test 是否已建立
- 既有錯誤是否已標記成 pre-existing
- 分析報告之間是否互相一致

---

# 2. 驗證型 PR

## 定義
這類 PR 的表面改動通常不大，但真正耗時在於：
**你必須證明某段東西真的能刪、能移、能停用。**

## 典型特徵
- 清 dead code / unused import / stale wrapper
- 移除欄位、helper、舊流程
- 看起來像小修，但一定要先證明「沒人用」

## PR2 為什麼屬於這類
PR2 做的事包括：
- 移除 `_last_log_flush`
- 移除 `preview_jar_extraction_service` import
- 移除 `preview_jar_extraction_service()` wrapper
- 清理 `main.py` 雜訊註解

真正花時間的不是刪除本身，而是要證明：
- `_last_log_flush` 沒使用點
- `preview_jar_extraction_service()` 沒 caller，而且還是壞 wrapper
- `__pycache__` 汙染 `grep`，得先清掉再驗證

## 最花時間的地方
- 全域搜尋引用點
- 區分 runtime caller 與文件提及
- 驗證替代路徑已經存在
- 避免被 `.pyc` / cache 汙染搜尋結果

## 開工前應準備
- 明確的刪除清單
- 搜尋指令 / grep / rg 規則
- 替代路徑與 caller 的確認方式

## 適合的 checklist 方向
- `rg/grep` 確認引用點
- import / runtime smoke test
- 若涉及刪除，PR 文件必須補六欄位刪除說明

---

# 3. 邊界型 PR

## 定義
這類 PR 不是在「多寫功能」，而是在處理：
- import 行為
- 啟動責任
- side effect
- service / UI / config 的邊界

## 典型特徵
- 改 module 初始化方式
- 把 top-level import 轉成 lazy import
- 收斂 entry point / bootstrap
- service façade / wrapper 分層整理

## PR3 為什麼屬於這類
PR3 做的事：
- 拿掉 `config_manager.py` 的 import-time side effect
- 把 `main.py` 的啟動責任收斂到 `bootstrap_runtime()`
- 把 `app/services.py` 的 config 存取改成包裝層

這類 PR 最危險的地方是：
- 不是單一檔案內能看完
- 要連 import chain、初始化時機、相容行為一起看
- 很容易把 `import main` / `from app import services` 這種本來能過的東西弄炸

## 最花時間的地方
- 釐清哪裡在偷跑 side effect
- 決定「功能不變但責任改去哪」
- 做完後還要驗證 import 不炸

## 開工前應準備
- import 依賴盤點
- entry point / bootstrap 路徑盤點
- guard test / hardcoded import path 盤點

## 適合的 checklist 方向
- `uv run python -c "import main"`
- `uv run python -c "from app import services"`
- full pytest baseline 對照
- `git diff --stat` 確認不要擴散

## 額外規則（從 PR5 學到的）
**凡涉及 package / module / import 結構變更的 PR，Phase 1 開始前必須先做 Phase 0 盤點：掃清所有 guard test、import 依賴點、硬編碼路徑，再進 Phase 1。**

這條規則就是為了避免 PR5 那種：
- 看起來只是補 import
- 實際上被 guard test 擋下來
- 中途才發現還有結構守門規則

---

# 4. 文件型 PR

## 定義
這類 PR 幾乎不動功能邏輯，核心目標是：
**把未來維護成本降下來。**

## 典型特徵
- 補註解
- 補 docstring
- 補邊界說明
- 把隱性規則寫成顯性文字

## PR4 為什麼屬於這類
PR4 補了：
- `app/services.py` 的意圖說明
- `app/task_session.py` 的狀態邏輯說明
- `app/views/extractor_view.py` 的 generator / poller 分工說明
- `main.py` 的啟動責任與 UI 組裝邊界
- `config_manager.py` 的 lazy config / logging 初始化說明

## 最花時間的地方
- 不是怎麼讓程式跑，而是怎麼把意圖寫清楚
- 需要判斷哪些註解是有價值的，哪些只是重述程式碼
- 要避免註解與實際行為不一致

## 開工前應準備
- 已穩定的行為與邊界結論
- 想寫給未來維護者看的重點
- 哪些地方最容易誤解

## 適合的 checklist 方向
- import / runtime smoke test
- `git diff` 確認只動註解 / docstring / whitespace
- PR 文件要寫清楚「本次不改功能」

---

# 怎麼用這份表

開新 PR 前，先問這 3 個問題：

1. **這顆最像哪一型？**
   - 盤點型 / 驗證型 / 邊界型 / 文件型

2. **時間會花在哪？**
   - 理解現狀？
   - 證明能刪？
   - 邊界與 side effect？
   - 文件品質？

3. **該先準備什麼？**
   - analysis bundle？
   - grep / rg 驗證指令？
   - Phase 0 import/guard test 盤點？
   - 註解與意圖清單？

如果一開始答得出來，PR 範圍、驗證方式、時間預估都會準很多。

---

# 一句話總結

> **不是每顆 PR 都是在寫 code；很多時候，真正花時間的是理解、驗證、收邊界，或把事情寫清楚。**
