# 重構總表：PR39B ~ PR58（邏輯不動 / 測試先行）

## Summary
本文件整理 `Minecraft_translator_flet` 在 **PR39B 之後** 的完整重構藍圖。

本輪重構的核心原則只有兩條：
- **不改邏輯，不偷帶行為變更**
- **所有重構都要先有測試護城河，再動刀**

這份總表的定位不是「願望清單」，而是可直接落地的執行順序與 PR 切法。

---

## 目前基線（2026-03-12）

### 已完成到哪裡
- PR31 ~ PR39A 已完成 non-UI 結構整理主線：
  - plugin shared helper 抽離
  - lang text detection helper 抽離
  - pipeline logging bootstrap 去重
  - non-UI guard tests 擴充
  - `lm_translator_main` Phase 1 切分
  - `lang_merger` Phase 1 切分
  - `cache_manager` 薄 façade
  - caller migration
  - `lm_translator_main` / `lang_merger` 最後相容層清理
- `app/services.py` 已收斂為 **只服務 QC/checkers 的殘留 façade**
- `main` 與 `origin/main` 已同步到 PR39A 完成點

### 尚未收尾的關鍵點
- `PR39B`：`cache_manager` 私有 state 相依尚未完全拔除
- `qc_view.py` / `app/services.py`：先凍結，不在本輪前段處理
- 大型 view 還沒有足夠 characterization tests，不適合太早重構

---

## 硬規則（整個專案都適用）

### 1. 不改邏輯
- 不改輸入/輸出契約
- 不改翻譯策略
- 不改 batch policy
- 不改 UI 行為
- 不順手修 unrelated bug

### 2. 一顆 PR 只做一種事
禁止混用：
- 結構拆分
- import migration
- dead code cleanup
- 行為修正
- UI 調整

### 3. 先測試，後重構
每顆 PR 固定流程：
1. `Phase 0` 盤點
2. 補 characterization / guard tests
3. 進入 Phase 1 重構
4. 跑 focused tests
5. 跑 full pytest
6. 更新 PR 文件

### 4. UI 晚於 core
- 先收 non-UI 邊界與 pipeline
- 再補 view characterization tests
- 最後才拆 view 大檔

### 5. `qc_view.py` 先凍結
在功能命運未決前：
- 不重寫
- 不下線
- 不遷移到新 UI 結構
- 只保留最小相容入口

---

## 每顆 PR 的固定交付物

每顆 PR 必須包含：
- `docs/pr/...md` 設計/交付文件
- `## Phase 0 盤點`
- `## Validation checklist`
- `## Rejected approaches`
- 對應 focused tests
- `full pytest` 結果

若該 PR 涉及刪除/替換，固定補：
- 為什麼改
- 為什麼能刪
- 目前 caller 誰在用 / 沒人在用
- 替代路徑
- 風險
- 驗證方式

---

# Phase A：先把目前這輪 non-UI 收乾淨

## PR39B：`cache_manager` state 封裝收尾
### 目標
完成 `cache_manager` 私有 state 收尾，讓 runtime 與 tests 都不再碰 private globals。

### 範圍
- `translation_tool/utils/cache_manager.py`
- `translation_tool/utils/cache_store.py`
- `translation_tool/core/lm_translator.py`
- `tests/test_cache_store.py`
- `tests/test_cache_search_orchestration.py`
- `tests/test_cache_manager_api_surface.py`

### 必補測試 / 驗證
- cache state holder 測試
- live-reference guard 測試
- runtime caller migration smoke
- full pytest

### 完成標準
- runtime 不再直接碰 `_translation_cache` / `_initialized`
- tests 不再直接操作 `cache_manager._xxx`
- `get_cache_dict_ref()` 契約維持成立
- full pytest 維持綠燈

---

## PR40：拆 `translation_tool/core/lm_translator.py`
### 目標
把 `translate_directory_generator()` 內部的 orchestration 分層，但保留它作為唯一入口。

### 範圍
建議拆出：
- 掃描/抽取準備
- cache hit / miss 分流
- cache-hit 寫回輸出
- dry-run preview 輸出
- batch translation loop glue
- output flush / summary helper

### 必補測試 / 驗證
新增建議：
- `tests/test_lm_translator_cache_split.py`
- `tests/test_lm_translator_dry_run.py`
- `tests/test_lm_translator_output_writeback.py`

### 完成標準
- `lm_translator.py` 保留入口，不再是單一巨大流程塊
- 行為完全不變
- focused tests + full pytest 綠

---

## PR41：整理 `translation_tool/core/lm_translator_shared.py`
### 目標
把 shared helper 層做真正模組邊界整理，避免 PR40 只是在表面拆一層。

### 範圍
建議整理成：
- preview helpers
- translation result recording
- cache/preview shared glue
- batch loop shared helper

### 必補測試 / 驗證
新增建議：
- `tests/test_lm_translator_shared_preview.py`
- `tests/test_lm_translator_shared_recording.py`

### 完成標準
- shared 模組責任更單純
- 上層入口與下層 shared 的依賴邊界清楚
- 行為不變、測試綠燈

---

## PR42：拆 `translation_tool/core/lang_merge_content.py`
### 目標
處理 `lang_merge_content.py` 的內容 patch / copy / cleanup 混裝問題。

### 範圍
建議依責任拆成：
- localized content patching
- content copy / quarantine policy
- pending export / cleanup helper

### 必補測試 / 驗證
新增建議：
- `tests/test_lang_merge_content_patchers.py`
- `tests/test_lang_merge_pending_export.py`

### 完成標準
- `lang_merge_content.py` 不再是 600+ 行的大混裝模組
- 對 `lang_merger` 主入口行為零改動
- focused tests + full pytest 綠

---

# Phase B：核心 pipeline 系列重構

## PR43：拆 `translation_tool/core/ftb_translator.py`
### 目標
把 FTB pipeline 從單體流程檔拆成可測的責任區塊。

### 範圍
建議拆分：
- raw export / clean
- template prepare
- LM translate handoff
- pipeline orchestration

### 必補測試 / 驗證
新增建議：
- `tests/test_ftb_translator_export.py`
- `tests/test_ftb_translator_clean.py`
- `tests/test_ftb_pipeline_smoke.py`

### 完成標準
- FTB 流程每段皆有 focused tests
- `run_ftb_pipeline()` 契約不變

---

## PR44：拆 `translation_tool/core/kubejs_translator.py`
### 目標
處理目前 non-UI 最大顆之一的 `kubejs_translator.py`。

### 範圍
建議拆分：
- JSON I/O helper
- clean / merge helper
- path/root resolve helper
- step1 / step2 / step3 orchestration

### 必補測試 / 驗證
新增建議：
- `tests/test_kubejs_cleaning.py`
- `tests/test_kubejs_pipeline_steps.py`
- `tests/test_kubejs_path_resolution.py`

### 完成標準
- `run_kubejs_pipeline()` 對外契約不變
- 大檔被切成責任明確的模組

---

## PR45：整理 `translation_tool/core/md_translation_assembly.py`
### 目標
整理 MD pipeline 的 step glue 與進度代理層。

### 範圍
建議處理：
- progress proxy
- step1 / step2 / step3 glue
- step statistics / logging helper

### 必補測試 / 驗證
新增建議：
- `tests/test_md_pipeline_steps.py`
- `tests/test_md_progress_proxy.py`

### 完成標準
- `run_md_pipeline()` 契約不變
- step orchestration 可獨立測試

---

## PR46：拆 `translation_tool/core/jar_processor.py`
### 目標
把 jar discovery / extract / preview / report 從單體檔案中切開。

### 範圍
建議拆分：
- jar discovery
- extract process
- preview generation
- preview report generation

### 必補測試 / 驗證
新增建議：
- `tests/test_jar_processor_find.py`
- `tests/test_jar_processor_extract.py`
- `tests/test_jar_preview_report.py`

### 完成標準
- `extract_lang_files_generator()` / `extract_book_files_generator()` / `preview_extraction_generator()` 契約不變
- preview/report 路徑可獨立測

---

## PR47：完成 `plugins/shared` 收斂
### 目標
把 FTB / KubeJS / MD 仍殘留的共用規則收回 shared 模組。

### 範圍
持續收斂：
- path rename rules
- lang text / already-zh 判定
- JSON read/write 小工具
- pending file traversal / dry-run stats

### 必補測試 / 驗證
新增建議：
- `tests/test_plugins_shared_lang_rules.py`
- `tests/test_plugins_shared_json_io.py`

### 完成標準
- plugin 之間重複 helper 明顯下降
- shared 契約有專屬測試保護

---

# Phase C：runtime / app / startup 邊界整理

## PR48：整理 `app/services_impl` 的共用 task runner / lifecycle
### 目標
把 pipeline service 的重複 lifecycle 處理抽成可重用 helper。

### 範圍
建議統一：
- generator update consume
- session lifecycle
- UI log handler binding
- common error wrapping

### 必補測試 / 驗證
在既有 `test_pipeline_logging_bootstrap.py` 之外新增：
- `tests/test_pipeline_services_session_lifecycle.py`
- `tests/test_pipeline_services_error_handling.py`

### 完成標準
- `services_impl/pipelines/*.py` 繼續保持薄檔案
- 共用流程進一層統一，但對外介面不變

---

## PR49：整理 `main.py` 啟動責任
### 目標
讓 `main.py` 更像 entrypoint，而不是半個總控檔。

### 範圍
建議抽出：
- `view_registry`
- `startup_tasks`
- 啟動後 index rebuild glue

### 必補測試 / 驗證
新增建議：
- `tests/test_view_registry.py`
- `tests/test_startup_tasks.py`

### 完成標準
- `main.py` 只負責 app entry / UI 組裝
- 啟動責任單純
- 不改 UI 行為

---

## PR50：整理 `config_manager` / `text_processor` 相容層
### 目標
降低 lazy config proxy 與舊式 config 匯入對結構的污染。

### 範圍
- 盤點哪些 caller 仍依賴 `from config_manager import config`
- 能改成顯式 `load_config()` 的地方就改
- 保留最低限度相容層，不硬砍

### 必補測試 / 驗證
新增建議：
- `tests/test_config_proxy_compat.py`
- `tests/test_text_processor_config_resolution.py`

### 完成標準
- `config` proxy 不再成為新耦合來源
- `text_processor` / config 解析路徑穩定

---

# Phase D：UI 層重構前的測試先行工程

## PR51：補大 view characterization tests（第一批）
### 目標
先補大 view 的行為護欄，避免日後拆 view 時像在盲飛。

### 範圍
優先：
- `app/views/translation_view.py`
- `app/views/extractor_view.py`
- `app/views/config_view.py`
- `app/views/rules_view.py`

### 必補測試 / 驗證
建議 coverage：
- 初始化是否成功
- 關鍵 control / handler 綁定
- service call glue
- session / progress / output 最小契約

### 完成標準
- 這 4 支大型 view 都有 characterization tests
- 後續 UI refactor 才有安全網

---

## PR52：補小 view characterization tests（第二批）
### 目標
把剩餘 view 補齊最低限度護欄。

### 範圍
- `app/views/merge_view.py`
- `app/views/lm_view.py`
- `app/views/icon_preview_view.py`
- `app/views/lookup_view.py`
- `app/views/bundler_view.py`

### 完成標準
- 所有活躍 view 至少有基本 characterization tests
- 後續拆 view 時不會完全裸奔

---

# Phase E：UI / view 結構重整（最後才做）

## PR53：拆 `app/views/cache_view.py`
### 目標
處理全 repo 最大檔 `cache_view.py`。

### 範圍
建議拆分：
- state
- actions/controller
- panel builders
- dialog helpers
- polling / refresh glue

### 必補測試 / 驗證
沿用既有 cache view tests，再補：
- panel wiring
- refresh/search/rebuild action 契約
- state transition 測試

### 完成標準
- `cache_view.py` 不再是 2500+ 行怪獸檔
- UI 行為不變

---

## PR54：拆 `app/views/extractor_view.py`
### 目標
把 extractor view 拆成可維護的 UI 結構。

### 範圍
建議拆分：
- file picker / path state
- extraction actions
- preview/report actions
- rendering helpers

### 完成標準
- view 邊界清楚
- 不改 extractor UI 行為

---

## PR55：拆 `app/views/translation_view.py`
### 目標
整理翻譯主畫面的 form / task glue / rendering 混裝。

### 範圍
建議拆分：
- form state
- task start/stop glue
- output options
- progress/log rendering helper

### 完成標準
- `translation_view.py` 可維護性明顯提升
- 不改操作流程

---

## PR56：拆 `app/views/rules_view.py` + `config_view.py`
### 目標
整理表單 / 儲存型 view 的 state 與 CRUD glue。

### 範圍
- `rules_view.py`
- `config_view.py`

### 必補測試 / 驗證
- load/save
- dirty state
- validation / dialog 行為

### 完成標準
- 兩支 view 轉為較乾淨的 state + action 結構
- 不改表單行為

---

# Phase F：最末端收尾

## PR57：清 dead code / compatibility leftovers
### 目標
在主要邊界都收乾淨後，做一次保守的 dead code / leftover cleanup。

### 前提
只能在前面各段落都完成後再做，避免誤刪 staging seam。

### 範圍
- wrapper / alias / compatibility import
- old helper / 無 caller 的保留 API
- 文檔與註解同步整理

### 完成標準
- repo 內的殘留相容層顯著下降
- 沒有 caller 的舊碼被安全移除

---

## PR58：決定 `qc_view.py` / `app/services.py` 最終命運
### 目標
最後才處理 QC 舊線，因為它本質上是「產品決策」+「結構處理」混合題。

### 可選路線
- **保留功能**：遷移到正式 service / view 架構
- **不保留功能**：先補測試 / 標記停用 / 再移除

### 前提
- 前面 core / app / main / view 主線重構已收乾淨
- 有能力明確判斷 QC 的功能命運

### 完成標準
- `app/services.py` 不再承擔歷史包袱
- `qc_view.py` 不再是懸空特例

---

# 建議執行順序（總覽）

1. PR39B — `cache_manager` state 收尾
2. PR40 — `lm_translator.py` orchestration 拆分
3. PR41 — `lm_translator_shared.py` 整理
4. PR42 — `lang_merge_content.py` 拆分
5. PR43 — `ftb_translator.py`
6. PR44 — `kubejs_translator.py`
7. PR45 — `md_translation_assembly.py`
8. PR46 — `jar_processor.py`
9. PR47 — `plugins/shared` 收斂
10. PR48 — `services_impl` lifecycle helper
11. PR49 — `main.py` registry / startup 邊界整理
12. PR50 — `config_manager` / `text_processor` 相容層整理
13. PR51 — 大 view characterization tests（第一批）
14. PR52 — 小 view characterization tests（第二批）
15. PR53 — `cache_view.py`
16. PR54 — `extractor_view.py`
17. PR55 — `translation_view.py`
18. PR56 — `rules_view.py` + `config_view.py`
19. PR57 — dead code / leftover cleanup
20. PR58 — `qc_view.py` / `app/services.py` 最終決策

---

# 測試策略（全專案統一）

## 一、Baseline rule
每顆 PR 前都先記錄：
- focused test baseline
- full pytest baseline

## 二、Characterization first
對尚未有足夠測試的大檔：
- 先補「現況行為測試」
- 再做結構重整

## 三、Focused + full
每顆 PR 至少跑：
- 對應 focused tests
- `uv run pytest -q --basetemp=... -o cache_dir=...`

## 四、禁止邊拆邊修 unrelated bug
若測試揭露既有 bug：
- 文件記錄
- 另開獨立修正 PR
- 不混在結構 PR 中

---

# 整體完成標準（專案層級）

當以下條件大致成立，才算這輪重構專案完成：
- core 巨檔都已按責任切分
- plugin shared 規則已收斂
- `services_impl` lifecycle 更統一
- `main.py` 啟動責任乾淨
- 大型活躍 view 都先有 characterization tests，再完成結構拆分
- dead code / leftover compatibility 已保守清理
- `qc_view.py` 不再是懸而未決的特殊點

---

# 明確不在本計畫前段處理的項目

以下內容在前段重構中**刻意不碰**：
- UI 改版 / 重新設計版面
- 功能增強
- 翻譯品質優化
- 效能調校
- 新格式支援
- QC 功能存廢決策（延後到 PR58）

---

# 最後判斷

這個專案現在最需要的不是再追新功能，而是把它從「能跑的系統」整理成「可持續維護的系統」。

正確節奏是：
- 先把 core / pipeline / startup 邊界收乾淨
- 再補 view 測試護城河
- 最後才拆大型 UI 檔案
- QC 舊線留到整體主線重構後再決策

這樣做最慢，但最穩；也是最不容易把整個專案搞成一團賭局的路線。
