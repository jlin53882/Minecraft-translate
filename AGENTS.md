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

## 常用檢查

- 測試：`python -m pytest tests -q`（UI smoke 相關測試需要 playwright，沒安裝時略過）
- Lint／格式：`ruff check <檔案>`、`ruff format <檔案>`（只格式化自己動到的檔案）
- 產生文件：新增 `except Exception` 後執行 `python tools/gen_exception_inventory.py`；
  修改設定 schema 後執行 `python tools/gen_config_example.py`
- 後台記錄與畫面訊息成對出現時文字必須一致（見 `tests/test_log_pair_contract.py`）

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **Minecraft-translate** (13684 symbols, 36964 relationships, 845 execution flows).

> Index stale? Run `node .gitnexus/run.cjs analyze --embeddings --index-only` from the project root — embeddings use the configured provider (on this machine, Ollama `qwen3-embedding:4b` on GPU). No `.gitnexus/run.cjs` yet? Bootstrap with `npx`, `bunx`, or `pnpm dlx` — e.g. `bunx gitnexus@latest analyze --embeddings` (npm 11 npx crash; #1939).

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

<!-- gitnexus:end -->
