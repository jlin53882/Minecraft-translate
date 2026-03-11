# PR13（設計）— app/services.py 分拆規劃（只做評估與設計，不改 code）

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 目標：針對 `app/services.py` 做架構評估，產出可實作的分拆方案與 PR 設計序列。
> 本輪狀態：已評估 / 可交付（未改任何程式邏輯）。

---

## 一句話總結

`app/services.py` 目前混合了「UI 任務協調、logging/節流、config/rules IO、各種 pipeline 入口、cache UI service」等多個 concern；建議以「維持對外 API 不變」為前提，採多顆小 PR 逐步把實作搬到 `app/services_impl/` 子模組，並由 `app/services.py` 持續擔任穩定 façade / re-export。

---

## 1) Phase 0：`app/services.py` 現況盤點

### 1.1 檔案規模與症狀
- 行數：約 979 行（偏大）
- 症狀：
  - 單檔同時處理 UI/session、log 節流、config IO、翻譯/抽取/檢查/打包等多條 pipeline
  - 任何小改動都容易造成「git diff 難 review」與「回歸風險難定位」

### 1.2 混合的主要 concern（SSOT）
1. **UI 任務協調（TaskSession 介接）**
   - 把 core generator/pipeline 的輸出 update_dict 轉寫到 `TaskSession`（log/progress/error/status）。

2. **Logging / handler / 節流**
   - `UISessionLogHandler` 的掛載/解除與 session 綁定。
   - `LogLimiter`（GLOBAL_LOG_LIMITER）用來降 UI 重繪頻率。

3. **config / replace_rules 的路徑與 IO 包裝**
   - `PROJECT_ROOT / CONFIG_PATH / REPLACE_RULES_PATH`
   - `_load_app_config()` / `_save_app_config()` + `load_replace_rules()` 等 wrapper

4. **各條 pipeline 的 service wrapper（翻譯/抽取/檢查/打包）**
   - LM 翻譯、Lang/Book 提取、FTB/KubeJS/MD 翻譯、合併、輸出打包、檢查器。

5. **Cache UI services（給 cache_view.py 用）**
   - `cache_get_overview_service()` / reload / save / search / update dst / rotate / rebuild index

6. **查詢/小工具類 service**
   - 例如 lookup/batch lookup 等。

### 1.3 主要依賴關係（摘要）
- 依賴 core：`translation_tool.core.*`（多個 pipeline 入口）
- 依賴 utils：`translation_tool.utils.config_manager`、`text_processor`、`species_cache`、`cache_manager`、`ui_logging_handler`
- 被 UI 呼叫：`main.py`、多個 `app/views/*.py`（尤其 cache_view/extractor_view/lm_view 等）

### 1.4 哪些適合先拆？哪些暫時不動較安全？

**適合先拆（風險較低、邊界清楚）：**
- Logging/節流與 handler 綁定（可集中且可測）
- config/rules IO wrappers（純 IO + 路徑）
- cache UI services（已經是一組獨立 API，可抽成 module）

**暫時不動較安全（先保留在 façade 或晚點拆）：**
- 各條 pipeline wrapper（因為牽涉 session 控制流程與大量 import；拆太快容易產生 circular import 或漏掉 UI 行為）

---

## 2) 建議的模組拆分圖（維持 app.services 對外 API）

建議新增資料夾：`app/services_impl/`。

> 關鍵決策：**不要**新增 `app/services/` package。  
> 原因：Python 匯入規則下，同名 package 會吃掉同名 module；若同時存在 `app/services.py` 與 `app/services/`，`import app.services` 會優先落到 package，造成既有 import 路徑行為改變。  
> 因此本案採 **方案 B**：保留 `app/services.py` 當穩定 façade，新增不同名的 `app/services_impl/` 作為實作承接層。

```
app/
  services.py                 # 穩定 façade：維持既有 import path；逐步 re-export
  services_impl/
    __init__.py               # 實作層聚合匯出（供 services.py re-export）
    logging_service.py        # UI_LOG_HANDLER / LogLimiter / update_logger_config / 共用保護工具
    config_service.py         # CONFIG_PATH / REPLACE_RULES_PATH / load/save wrappers
    cache_services.py         # cache_*_service（給 cache_view.py 用）
    pipelines/
      __init__.py
      lm_service.py           # run_lm_translation_service
      extract_service.py      # run_lang_extraction_service / run_book_extraction_service
      ftb_service.py          # run_ftb_translation_service
      kubejs_service.py       # run_kubejs_tooltip_service
      md_service.py           # run_md_translation_service
      bundle_service.py       # run_bundling_service
      checkers_service.py     # untranslated/variant/english_residue/tsv compare
      merge_service.py        # run_merge_zip_batch_service
    lookup_service.py         # manual/batch lookup
```

**邊界原則：**
- `logging_service.py` 負責「如何把 log 送進 UI」與「如何節流」。
- `pipelines/*` 只負責把 core pipeline 包成 service 介面；不包含 cache UI 相關行為。
- `cache_services.py` 專注 cache_view 需要的讀寫/搜尋/rebuild 封裝。
- `services.py` 長期目標是瘦身成「薄 façade」，但持續保留，確保既有 `from app.services import ...` 不被破壞。
- 採用 `services_impl/` 而不是 `_services/`，是為了避免私有模組語意，讓名稱更清楚表達「實作承接層」。

---

## 3) 建議的 PR 序列（逐顆、小步、可回退）

> 命名：沿用你既有慣例（PR13 起跳）。

### Phase 0（本文件）
- 目標：完成 inventory + 分拆設計（只寫文件，不改 code）
- 驗證：import/smoke（不捏造）

### PR13 — 建立 `services_impl` 骨架 + façade re-export（最小可行）
- 目標：新增 `app/services_impl/` 目錄（空殼），`app/services.py` 保持可用
- Scope：只做結構與 re-export（不搬邏輯）
- Out-of-scope：不動任何 pipeline 內容
- 驗證：`tests/test_main_imports.py`、`uv run python -c "import app.services"`
- 風險：路徑/命名錯誤造成 import fail

### PR14 — 抽離 logging/節流到 `services_impl/logging_service.py`
- 目標：把 `LogLimiter`、`GLOBAL_LOG_LIMITER`、`UI_LOG_HANDLER`、`update_logger_config()` 等集中
- Scope：移動程式碼 + `services.py` re-export
- Out-of-scope：不改各 pipeline wrapper 行為
- 驗證：與 UI/cache 相關 import tests + 既有 pytest
- 風險：handler 綁定點漏掉導致 UI 沒 log
- 搬移策略（必補）：
  - Phase 0 先盤點目前 handler attach/detach 發生在哪些 service 入口
  - PR14 內新增至少一個 smoke 驗證，確認 handler 在任務開始後仍會正確掛上

### PR15 — 抽離 config/rules IO 到 `services_impl/config_service.py`
- 目標：集中 `PROJECT_ROOT/CONFIG_PATH/REPLACE_RULES_PATH` 與 load/save wrappers
- 驗證：`tests/test_main_imports.py` + 讀取 config/rules 的 smoke
- 風險：路徑變更造成讀錯檔；必須保持 PR8 的「以 PROJECT_ROOT 為準」不回退

### PR16 — 抽離 cache UI services 到 `services_impl/cache_services.py`
- 目標：把 `cache_*_service()` 群組搬走
- 驗證：`tests/test_cache_view_features.py`、`tests/test_cache_search_orchestration.py`、cache_view import
- 風險：cache_view 依賴的回傳格式/欄位名不可變
- 依賴：**需建立在 PR12 search orchestration 已穩定落地之後**，避免把舊版 cache/search 路徑搬進新模組

### PR17 之後 — 依 pipeline 類型逐顆抽離（lm/extract/ftb/kubejs/md/checkers/bundle/merge/lookup）
- 目標：每顆 PR 只移一組 pipeline wrapper（避免超大 PR）
- 驗證：對應 view 的 import smoke + 關聯 pytest
- 風險：circular import（尤其 services ↔ views）；必要時採「在函式內 import」策略保持啟動順序
- 備註：PR17 之後每顆實作 PR 開工前，都應各自補一份該顆的實作設計稿，不直接跳過細節設計

---

## 4) 風險點與回歸點

### 風險點
- **Circular import**：views 匯入 services，而 services 又匯入 view（應避免）。
- **UI_LOG_HANDLER 綁定時機**：搬移後若 handler 初始化順序改變，UI log 可能不再顯示；PR14 應先盤點 attach/detach 點，再加 smoke 驗證。
- **cache_search_service 回傳格式**：cache_view 有 UI 假設（items/truncated/limit）。
- **模組命名衝突**：不得使用 `app/services/` 與 `app/services.py` 同名並存；需固定採 `services_impl/` 路線。

### 回歸點（rollback 策略）
- 每顆 PR 都保留 `app/services.py` façade API；若新模組出問題可快速回退到原實作。

---

## 5) 本輪新增文件
- `docs/pr/2026-03-11_2030_PR_pr13-services-splitting-design.md`（本文件）

---

## 6) 驗證（本輪不改 code 的 smoke）
- `uv run python -c "import app.services as s; print('ok', hasattr(s,'run_lm_translation_service'), hasattr(s,'cache_get_overview_service'))"`

---

## 7) 需要家豪先決策的點
1. **是否同意採 `app/services.py` façade + `app/services_impl/` 實作層**：
   - 我建議同意。
   - 原因：`app/services.py` 與 `app/services/` 不能安全並存；若硬做同名 package，`import app.services` 會優先落到 package，破壞既有 import 行為。
   - 採 `services_impl/` 能保留既有 import 相容，同時讓實作可漸進搬移。

2. **拆分優先順序是否固定為：logging → config/rules → cache_services → pipelines**：
   - 我建議照此順序，因為前 3 個最容易獨立、風險最低。
