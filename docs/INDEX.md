# Minecraft-translate 文件總索引

> 本索引幫助 AI 和人類快速找到所需文件。所有路徑相對於專案根目錄。

---

## 必讀文件（新進/交接必看）

[README.md](../README.md) — 專案概覽、安裝、使用說明與整體架構

[docs/ROADMAP_CURRENT.md](./ROADMAP_CURRENT.md) — 當前已完成的 PR 序列與未來規劃

[docs/PR_WORKFLOW.md](./PR_WORKFLOW.md) — PR 工作流程標準（含 Phase 1/Phase 2 框架）

[docs/DOCSTRING_SPEC.md](./DOCSTRING_SPEC.md) — 程式碼文件中文化規範（維護性註解標準）

[docs/TYPE_SYSTEM.md](./TYPE_SYSTEM.md) — 型別系統與 Pydantic 模型設計說明

---

## 開發指南

[docs/GH_WORKFLOW.md](./GH_WORKFLOW.md) — GitHub CI/CD 工作流與觸發條件

[docs/RELEASE_WORKFLOW.md](./RELEASE_WORKFLOW.md) — 版本發布流程與版本治理規則

[docs/PR_EXECUTION_TYPES.md](./PR_EXECUTION_TYPES.md) — PR 執行類型參考（盤點型/驗證型/邊界型/文件型）

---

## 架構文件

[docs/DESIGN_OPTIMIZATION_REPORT.md](./DESIGN_OPTIMIZATION_REPORT.md) — 整體設計優化評估報告

[docs/cache_search_optimization.md](./cache_search_optimization.md) — SQLite FTS5 快取搜尋效能優化記錄（49秒→4秒，91%提升）

---

## 維護記錄

[docs/PR_ROADMAP_FUTURE.md](./PR_ROADMAP_FUTURE.md) — 未來 PR 規劃（含 PR62-71 藍圖，適用版本 v0.6.0）

[docs/PR40-58_REFACTOR_SUMMARY.md](./PR40-58_REFACTOR_SUMMARY.md) — PR40–PR58 重構總結（Phase 1 結構化收斂輪次）

---

## PR 設計稿（`docs/pr/`）

共 **134 個**設計檔，主要分類如下：

### UI 重構
- `2026-03-19_2118_merge_ui_layout_optimization_notes.md` — Merge View UI 布局優化
- `2026-03-19_2128_merge_ui_responsive_layout_report.md` — Merge View 響應式佈局報告

- `2026-03-19_2155_merge_ui_final_polish.md` — Merge UI 最終優化
- `2026-03-16_2312_PR_cache_view_split_design.md` — Cache View 拆分設計
- `20260316_view_refactor_design.md` — View 重構設計

### 核心重構
- `2026-03-12_1505_PR_pr40-lm-translator-orchestration-split-design.md` — LM Translator 協調拆分（PR40）
- `2026-03-12_1505_PR_pr42-lang-merge-content-split-design.md` — Lang Merge 內容拆分（PR42）
- `2026-03-12_1505_PR_pr46-jar-processor-split-design.md` — JAR Processor 拆分（PR46）
- `2026-03-12_1505_PR_pr48-services-impl-task-runner-lifecycle-design.md` — Task Runner 生命週期（PR48）
- `2026-03-12_1505_PR_pr49-main-entrypoint-boundary-design.md` — Main 入口邊界（PR49）
- `2026-03-12_1505_PR_pr53-cache-view-split-design.md` — Cache View 拆分（PR53）
- `2026-03-12_1505_PR_pr54-extractor-view-split-design.md` — Extractor View 拆分（PR54）
- `2026-03-12_1505_PR_pr55-translation-view-split-design.md` — Translation View 拆分（PR55）
- `2026-03-12_2341_PR_pr56-rules-and-config-view.md` — Rules/Config View（PR56）
- `2026-03-12_2344_PR_pr57-dead-code-and-compat-cleanup.md` — 廢棄程式碼清理（PR57）
- `2026-03-13_0004_PR_pr59-ui-text-theme-style-cleanup.md` — UI 文字主題清理（PR59）

### 測試
- `2026-03-13_1800_PR62_test_coverage_health_check.md` — 測試覆蓋率健檢（PR62）
- `2026-03-13_1800_PR63_test_fixture_refactor.md` — 測試 Fixture 重構（PR63）
- `2026-03-13_1900_PR63_test_infrastructure.md` — 測試基礎設施（PR63）
- `2026-03-12_2309_PR_pr51-large-view-characterization-tests.md` — 大型 View 表徵測試（PR51）
- `2026-03-12_2315_PR_pr52-small-view-characterization-tests.md` — 小型 View 表徵測試（PR52）
- `2026-03-12_1232_PR_pr33-phase1-validation-hold.md` — Phase 1 驗證保留點（PR33）

### 效能優化
- `2026-03-13_1800_PR66_cache_performance.md` — 快取效能優化（PR66）
- `2026-03-13_1900_PR66_cache_monitoring.md` — 快取監控（PR66-A）
- `2026-03-13_1900_PR67_lazy_load_optimization.md` — 懶載入優化（PR67）
- `2026-03-19_1518_PR_merge_pipeline_optimization_design_executable.md` — Merge Pipeline 優化設計
- `2026-03-19_1602_PR_merge_pipeline_optimization_design_v2.md` — Merge Pipeline 優化設計 v2
- `2026-03-19_1628_PR_jar_extract_optimization_design_final.md` — JAR 提取優化設計

### 主題/UI 元件
- `2026-03-13_1900_PR68_ui_component_extraction.md` — UI 元件抽取（PR68）
- `2026-03-13_1900_PR69_theme_convergence.md` — 主題系統收斂（PR69）
- `2026-03-13_1900_PR69_theme_establishment.md` — 主題系統建立（PR69）

### 錯誤處理
- `2026-03-13_1900_PR70_dead_code_cleanup.md` — 廢棄程式碼清理（PR70）
- `2026-03-13_1900_PR71_error_handling_unification.md` — 錯誤處理統一（PR71）
- `2026-03-13_1900_PR71_exception_consistency.md` — Exception 一致性評估（PR71）

### Docstring/文件
- `2026-03-13_1800_PR64_docstring_completion.md` — Docstring 補完（PR64）
- `2026-03-13_1800_PR65_docs_update.md` — 文件更新（PR65）

---

## Changelog（`docs/changelog/`）

共 **6 個**變更日誌/實作記錄：

[A1_A3_IMPLEMENTATION_SUMMARY.md](./changelog/A1_A3_IMPLEMENTATION_SUMMARY.md) — A1/A3 實作總結

[A1_A3_USAGE.md](./changelog/A1_A3_USAGE.md) — A1/A3 使用說明

[FIX_UI_SEARCH.md](./changelog/FIX_UI_SEARCH.md) — UI 搜尋修復記錄

[JAR_EXTRACTION_ENHANCEMENTS.md](./changelog/JAR_EXTRACTION_ENHANCEMENTS.md) — JAR 提取增強記錄

[ROLLBACK_IF_NEEDED.md](./changelog/ROLLBACK_IF_NEEDED.md) — 回滾計畫（如需時）

[UI_INTEGRATION_COMPLETE.md](./changelog/UI_INTEGRATION_COMPLETE.md) — UI 整合完成記錄
