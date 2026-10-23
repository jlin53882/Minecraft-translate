# Minecraft-translate 開發準則

## 程式規模：行數是防護欄，不是重構目標

行數只用來提醒「可能出現職責膨脹」。**請依職責、ownership、依賴邊界與控制流程複雜度拆分，
不要為了通過行數門檻而拆檔或抽函式。**

自動檢查在 `tests/test_view_size_limits.py`（只掃 `app/`）：

| 項目 | 正常 | 審查區（ratchet） | 硬擋 |
|---|---|---|---|
| 函式 | < 80 行 | 80–149 行 | >= 150 行 |
| View 檔案（`app/views/`） | < 800 行 | 800–1199 行 | >= 1200 行 |

- 審查區以 `tests/data/size_baseline.json` 記錄現況：**新進入審查區、或已記錄的項目變長才失敗**；
  縮短或消失不會失敗。
- 變長是合理的（例如同一職責的宣告式 Flet 版面）時，用
  `UPDATE_SIZE_BASELINE=1 pytest tests/test_view_size_limits.py` 更新快照，
  並在 PR 說明為什麼不需要拆。
- 複雜度（近似 cyclomatic）> 12、巢狀 > 4 只輸出警告，不擋 CI；新寫的程式請避免。

### 拆分原則

- 一個 module 代表一個 coherent responsibility。UI、檔案系統、worker 生命週期、
  領域邏輯、設定持久化混在同一處才是需要拆的「大檔案」。
- 單一職責的宣告式 UI 版面因宣告自然變長是可以接受的，不要硬拆成
  `view_layout.py`／`view_controls.py`／`view_helpers.py`。
- **禁止假拆分**：不要只為了把函式壓到門檻下，就抽出沒有獨立語義、沒有明確 ownership、
  只被單一函式機械呼叫的 helper（例如 `_continue_step()`、`_run_part2()`、`_process_more()`）。
  拆出來的函式要能回答「它負責什麼」（例如 `_validate_merge_inputs()`、`_collect_merge_summary()`）。

### 架構指標（不依賴行數）

`tests/test_architecture_metrics.py`（工具：`tools/architecture_metrics.py`，掃 `app/` 與 `translation_tool/`）
統計每個模組的複雜度、公開 API 數、class method 數、模組層級 import 深度、頂層 class／函式數與型別註解覆蓋率。

- 只有兩項會失敗：**新的循環 import**，以及**新增且不在基線的函式複雜度 > 20**。其餘指標變差只輸出警告。
- 基線在 `tests/data/architecture_baseline.json`，規則同 size baseline（只有變差才失敗，改善不失敗）；
  合理時用 `UPDATE_ARCH_BASELINE=1 pytest tests/test_architecture_metrics.py` 更新並在 PR 說明。
- `python tools/architecture_metrics.py` 印出摘要。行數防護欄保留，兩者並行。

## 常用檢查

- 測試：`python -m pytest tests -q`（UI smoke 相關測試需要 playwright，沒安裝時略過）
- Lint／格式：`ruff check <檔案>`、`ruff format <檔案>`（只格式化自己動到的檔案）
- 產生文件：新增 `except Exception` 後執行 `python tools/gen_exception_inventory.py`；
  修改設定 schema 後執行 `python tools/gen_config_example.py`
- 後台記錄與畫面訊息成對出現時文字必須一致（見 `tests/test_log_pair_contract.py`）

## 資料庫查詢：禁止臨時排序

`translation_tool/translation_db/` 的列表、分頁與候選查詢，`EXPLAIN QUERY PLAN` 不得出現
`USE TEMP B-TREE FOR ORDER BY`：臨時排序要先讀完整個候選集，成本隨資料量成長（70 萬筆時動輒數百 ms）。
請用索引（排序欄位與索引欄位順序一致）、先選當頁 id 再讀完整欄位，或由觸發器維護的衍生欄位來取得順序。

- 檢查在 `tests/test_moddb_no_temp_sort.py`。確實無法避免時，把查詢加進該檔的 `ALLOWED_PLAN_MARKERS`／`ALLOWED`，
  並寫明原因（排序量有明確上界，例如頁大小、缺譯筆數、不同的 kind 數）。
- 新增衍生資料或索引的規範與驗證方式見 `docs/developer-guide/performance.md`。

## 文件維護

- 文件只描述「現在」的程式與規則：不寫 PR／issue 編號、「原本／改為／已移除」等演進過程，也不寫「為什麼要做這次修改」；
  這些放在 commit message 與 PR 說明。例外：`CHANGELOG.md` 與 `docs/adr/`（決策紀錄）。
- `docs/` 只放長期維護文件（架構、契約、操作說明、ADR、產生文件）。測試風險盤點、實機驗證清單、UI 設計稿、審查與收尾紀錄這類
  「某個時間點的作業紀錄」不放進 repo，放在維護者的 Obsidian 筆記。
- 修改頁面（`app/views/`）的使用者流程、背景工作或錯誤處理時，同步更新 `docs/page_flows/<key>.md`
  與對應的 `docs/developer-guide/views/*_VIEW_ARCHITECTURE.md`；新增頁面時一併新增流程文件並列入 `docs/page_flows/README.md`。
- 文件中的程式路徑、`docs/page_flows/` 中反引號標示的識別字，以及 PR／issue 編號由 `tests/test_docs_code_paths.py` 檢查；
  `docs/developer-guide/views/*_VIEW_ARCHITECTURE.md` 的識別字由 `tests/test_view_docs_identifiers.py` 檢查。刪除或改名程式碼後跑這兩個測試。

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **Minecraft-translate** (22297 symbols, 60075 relationships, 1076 execution flows).

> Index stale? Run `node .gitnexus/run.cjs analyze --index-only` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? Bootstrap with `npx`, `bunx`, or `pnpm dlx` — e.g. `bunx gitnexus@latest analyze` (npm 11 npx crash; #1939).

## Always Do

- **MUST run impact before editing.** Use `impact({target: "symbolName", direction: "upstream"})` or `node .gitnexus/run.cjs impact "symbolName" --direction upstream --repo .`; report callers, processes, and risk. Never substitute grep for graph analysis.
- **MUST analyze graph changes before committing.** Use `detect_changes({scope: "all"})` (MCP) or `node .gitnexus/run.cjs detect-changes --scope all --repo .` (CLI fallback). `partial: true` or `truncated: true` is not a clean check — a zero means unseen, not unaffected; re-run it. For regression review: `detect_changes({scope: "compare", base_ref: "main"})` or `node .gitnexus/run.cjs detect-changes --scope compare --base-ref "main" --repo .`.
- MUST warn on HIGH/CRITICAL `risk` pre-edit; never use `riskSharedAxes` to waive a HIGH/CRITICAL `risk` warning. Compare File/symbol: MCP File omits axes; Graph-RAG expands File.
- **MUST treat `risk: UNKNOWN` as unresolved, not as low.** An empty caller set is not evidence the symbol is unused — it can also mean the callers are not resolvable by the index (plain-object property access, dynamic dispatch, cross-language calls). `impact` pairs `UNKNOWN` with a `riskNote` saying so. Confirm with a text search before treating the symbol as safe to change or delete; do not proceed on the strength of a zero.
- **MUST use `query({search_query: "concept"})` for concepts/flows, `context({name: "symbolName"})` for a named symbol, or `impact` for blast radius, on read-only callers, dependencies, imports, or execution flow.** Graph first; text search only for empty/`UNKNOWN`/literals.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method before MCP/CLI impact analysis.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis, and never read `UNKNOWN` as an all-clear — it means the walk could not answer, which is the one verdict that requires confirming by other means.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit before MCP/CLI graph change analysis.

## Resources

| Resource | Use for |
| --- | --- |
| `gitnexus://repo/Minecraft-translate/context` | Codebase overview, check index freshness |
| `gitnexus://repo/Minecraft-translate/clusters` | All functional areas |
| `gitnexus://repo/Minecraft-translate/processes` | All execution flows |
| `gitnexus://repo/Minecraft-translate/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
| --- | --- |
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
