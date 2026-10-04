# 第二輪重構審查（PR #106 之後）

> 量測日期：2026-10-01，基準：PR #106 分支（`ccr-013dc142-ycfk3o`）。
> 量測方式為靜態分析（`ruff` 0.16.9 預設規則，與 `uv.lock` 鎖定版本相同；`ast`、`grep`）與在乾淨 checkout 實際跑全部測試。
> **沒有逐項在真實執行中驗證**；動手前請先重新量測（指令在文末）。
> 第一輪審查見 `docs/REFACTOR_PLAN.md`（P0 / P1 已在 PR #106 處理或轉為 issue）。

## 規模

| 範圍 | Python 行數 |
|---|---|
| `app/` | 約 29,000 |
| `translation_tool/` | 約 23,500 |
| `tests/`（223 個檔案、2638 個測試） | 約 46,000 |
| `tools/` | 約 1,500 |

## 發現與處理方式

| # | 發現 | 嚴重度 | 追蹤 |
|---|---|---|---|
| 1 | 巨型 View / 函式：`cache_view.py` 3699 行、`icon_preview_view.py` 2175 行；超過 100 行的函式共 82 個（最長 `translate_batch_smart_old` 830 行） | 中（可維護性） | #114（View）、#115（引擎） |
| 2 | 三個 translator 流程相似、可抽共用骨架；設定硬寫、無 schema | 中 | #115 |
| 3 | `except Exception` 99 處、`try/except/pass` 24 處、非工具程式碼的 `print(` 35 處 | 中 | #115 |
| 4 | 型別標註風格未現代化（UP006 178 / UP035 64 / UP045 26）、import 未排序（53）、F401 36 / F841 4 | 低（可自動修） | #115 |
| 5 | **B023**：迴圈內定義的函式沒綁定迴圈變數，共 34 處（`kubejs_tooltip_inject.py` 20、`ftbquests_lmtranslator.py` 14）。抽查的案例目前不會出錯（同一次迴圈內立即使用），但寫法脆弱 | 低（潛在） | #115（本輪新增） |
| 6 | `ftbquests_lmtranslator.py` 被覆蓋的重複 callback（F811） | 低 | #116 |
| 7 | 分層：`translation_tool/core/lang_item_row.py` 仍 import flet 與 app；`app/views` 有 26 個檔案直接 import `translation_tool` | 中 | #115（lang_item_row）、#114（views 收斂） |
| 8 | 設定生效時機不一致：快取資料夾變更不會重載、`lm_translate_folder_name` 與 `species_cache.*` 需重啟 | 中 | #117 |
| 9 | **Bug**：`lang_merger.py` 讀錯 `replace_rules_path` 的位置，設定被忽略 | 中（行為不符預期） | #118 |
| 10 | **測試污染根目錄**：跑完整測試會留下 `C:`、`D:\fake\output`、`subdir/`、`logs/`、`學名資料庫/`、`快取資料/`、`.icon_cache/`；測試檔以 PR 編號命名；3 個測試讀原始碼文字 | 中 | #119 |
| 11 | 文件過期：13 份 View 架構文件、`PR_EXECUTION_TYPES.md`、`PROJECT_INDEX.md`、README 結構段；根目錄 release notes / SOP 散落；`tools/` 剩餘腳本 | 中 | #120 |
| 12 | UI 相容層債：舊色名稱映射（12 檔、約 340 處引用）、舊 `components.py`（與 `kit` 重疊） | 中（PR #106 自己留下的） | #121 |
| 13 | 相依套件：`httpx`、`tqdm`、`google-genai` 無 import；`pytest` 重複；`flet-*` 與 `flet[all]` 疑似重複；`description` 為預設文字 | 低 | #115 |
| 14 | 無 ruff 設定檔，CI 只對 PR 變更檔 lint | 低 | #115 |

「已存在 issue 補充 vs 另開」的判斷：

- **補充**（#114、#115）：屬於同一主題、只是缺數字或缺項目（巨型函式、lint、分層、相依套件、B023）。
- **另開**（#119、#120、#121）：主題獨立、驗收標準不同、可由不同人在不同時間處理（測試衛生、文件、UI 相容層）。

## 建議順序

### PR-B 進度（2026-10-04）

- `LangItemRow` 已從 `translation_tool/core/lang_item_row.py` 移至 `app/views/icon_preview_row.py`，
  `translation_tool/core` 不再反向 import Flet／`app`；相關 import boundary test 與 Icon View 文件已同步。
- `cache_view.py` 的總覽按鈕／empty state 已改用 `app.ui.kit`；`components.py` 尚未刪除，因 legacy
  tests 仍直接覆蓋其 public helpers。#121 的相容層清理仍未完成，不能在本 PR 宣稱關閉。

1. **#118**（bug，小、先補測試）→ **#119**（讓新測試不再污染）→ **#116**（死碼）。這三項小而獨立，風險低。
2. **#117** 的高優先項（快取資料夾重載、存檔時有任務的提示）。
3. **#114 + #121 + #120**：拆 View 時一併清除相容層、最後重寫 View 文件（避免重寫兩次）。
4. **#115**：引擎重構，依子項目拆成多個小 PR；先加 ruff 設定檔與自動可修的項目（型別標註、import 排序、未使用 import）。

## 不在範圍內 / 沒做

- 沒有評估效能（啟動時間、翻譯吞吐量）。
- 沒有做安全審查；已知 `config.json` 以明文儲存 API 金鑰（已在 `.gitignore`），ZIP 讀取有預算與安全檢查（`zip_safety`）。
- 沒有核對引擎端各模組的測試覆蓋率。

## 重現指令

```bash
# 規模與最大檔案
find app translation_tool -name '*.py' | xargs wc -l | sort -rn | head -25

# lint 統計（不含 tests）
ruff check app translation_tool main.py --statistics

# B023 分布
ruff check app translation_tool main.py --select B023 --output-format concise

# 超過 100 行的函式：用 ast 走訪 FunctionDef 的 end_lineno - lineno + 1

# 測試污染：在乾淨 checkout 跑完整測試後
git archive HEAD | tar -x -C /tmp/check && cd /tmp/check && pytest -q && ls -a

# 相依套件實際 import
grep -rln "^\s*\(import\|from\) <模組>" app translation_tool main.py tools
```
