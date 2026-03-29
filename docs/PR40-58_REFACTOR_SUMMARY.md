# PR40–PR58 Refactor Summary

## Scope
這一輪主線從 PR40 一路做到 PR58，目標不是單一功能，而是把整個 `Minecraft-translate` 專案的：
- translation core
- pipeline core
- service wrapper
- main entrypoint
- shared helper
- active views
- legacy compat seam

做完一輪可驗證、可回退、可維護的結構化收斂。

最終結果：
- `main` 已合到 `8c61917`
- full pytest 最終結果：`171 passed`
- 目前仍有 `61 warnings`，已確認為獨立的 Flet deprecation 議題，未混入本輪主線修補

---

## Execution rhythm used in this round
本輪固定採用以下節奏，並已實際驗證可行：
1. Phase 1
2. checklist 驗證
3. 寫 PR 文件
4. commit
5. push
6. 再回報

補充原則：
- 問題先自行檢查與收斂
- 只有真的需要產品/方向決策時才停下來問
- 每顆 PR 都保留對應 `docs/pr/*.md`
- 長期參考知識另外整理到 `docs/`，不混在 `docs/pr/`

---

## PR-by-PR summary

### PR40 — lm_translator orchestration split
- 拆出 scan/extract seam
- 補 dry-run / output writeback characterization tests
- 讓 `lm_translator.py` 開始從巨型入口退層

### PR41 — lm_translator shared boundary
- 拆 `lm_translator_shared.py` 成：
  - shared_cache
  - shared_preview
  - shared_recording
  - shared_loop
- 保留 façade，不做 caller migration

### PR42 — lang merge content split
- 拆 `lang_merge_content.py` 成：
  - patchers
  - copy
  - pending
- 保留 wrapper 與 monkeypatch seam

### PR43 — ftb translator split
- 拆 `ftb_translator.py` 成：
  - export
  - clean
  - template
  - orchestration
- 補第一批 FTB 專屬 focused tests

### PR44 — kubejs translator split
- 拆 `kubejs_translator.py` 成：
  - paths
  - io
  - clean
- 主檔保留 step orchestration

### PR45 — md translation assembly split
- 拆 Markdown pipeline 的：
  - progress proxy
  - stats
  - step glue
- 讓 `run_md_pipeline()` 回到入口 façade 角色

### PR46 — jar processor split
- 拆 `jar_processor.py` 成：
  - discovery
  - extract
  - preview/report
- 為 extractor view 後續拆分先清 core 邊界

### PR47 — plugins shared convergence
- 不做大搬家
- 只把真正穩定、跨 plugin 共用的 helper 正式收斂成 `plugins/shared` public API
- 補 shared public API focused tests

### PR48 — services impl task runner lifecycle
- 抽出 `_task_runner.py`
- 收斂 FTB / KubeJS / MD service wrapper 的共同 lifecycle
- 保留各 wrapper 的 monkeypatch seam

### PR49 — main entrypoint boundary
- 抽出：
  - `app/view_registry.py`
  - `app/startup_tasks.py`
- `main.py` 保留 entrypoint + UI 組裝本體

### PR50 — config proxy and text processor
- 不硬砍 `LazyConfigProxy`
- 新增 `config_access.py`
- 讓 `text_processor.py` 先改走顯式 helper
- legacy seam 保留，但內部導向新路徑

### PR51 — large view characterization tests
- 替大型活躍 view 補 characterization tests：
  - translation_view
  - extractor_view
  - config_view
  - rules_view
- 建立後續 view split 的測試護城河

### PR52 — small view characterization tests
- 補第二批活躍 view characterization tests：
  - lookup_view
  - bundler_view
  - lm_view
  - merge_view
  - icon_preview_view

### PR53 — cache view split
- 對 `cache_view.py` 採保守拆法：
  - state
  - history store
  - action runner
- 不直接大拆主類別，優先保相容

### PR54 — extractor view split
- 拆 `extractor_view.py` 成：
  - state
  - panels
  - actions
- 主類別公開方法名保留

### PR55 — translation view split
- 拆 `translation_view.py` 成：
  - state
  - panels
  - actions
- 仍維持同頁三 tab 模型，不改 UI 導航

### PR56 — rules and config view
- 拆 `config_view.py` 成：
  - config_form
  - config_actions
- 拆 `rules_view.py` 成：
  - rules_state
  - rules_actions
  - rules_table
- 保留主檔 façade 與 guard seam

### PR57 — dead code and compat cleanup
- 只刪有完整 caller 證據支持的殘留殼：
  - `run_generator_task()`
  - extractor 舊 preview v1 helper
  - rules view 殘留 wrapper
- 明確保留：
  - `app/services.py`
  - `LazyConfigProxy`

### PR58 — qc view and app.services final decision
- 最終決策：
  - `qc_view.py` 保留
  - `app/services.py` 保留
  - 本顆不改 QC 內容
- 新增 QC characterization / façade tests，把「保留決策」落成工程事實

---

## Major architectural outcomes

### 1. Translation core 已退層
本輪完成：
- `lm_translator.py` orchestration seam
- `lm_translator_shared.py` 分層
- `lang_merge_content.py` 分層

成果：
- helper / content / cache / loop 責任邊界明確很多
- 後續改行為時更容易定位 impact

### 2. Pipeline core 已退層
已完成：
- FTB
- KubeJS
- Markdown
- JAR processor

成果：
- 各 pipeline 不再是一顆大雜燴檔案
- focused tests 已建立

### 3. Service lifecycle 已收斂
- FTB / KubeJS / MD wrapper 共享 `_task_runner.py`
- UI handler / session lifecycle / error wrapping 有共同控制流

### 4. Entry / shared / config access 已收斂
- `main.py` 不再同時持有 registry + startup tasks + UI glue 雜項
- `plugins/shared` 有了較明確 public API
- config access 開始往顯式 helper 收斂

### 5. Active views 已完成第一輪拆分與護欄
已處理：
- translation
- extractor
- cache
- config
- rules
- 以及多數活躍 view characterization tests

成果：
- view split 不再是盲飛
- monkeypatch seam 也較有意識地被保住

### 6. QC 線正式被定義為「保留的凍結支線」
- 不當作 dead code 亂刪
- 也不在這輪假裝把它重寫完
- 後續應開新主題處理

---

## What was intentionally not done
這輪刻意沒有做的事：
- 不在主線混入 Flet deprecation cleanup
- 不在 PR58 對 `qc_view.py` 做內容級改動
- 不硬刪 `app/services.py`
- 不硬刪 `LazyConfigProxy`
- 不把所有 shared/helper 一次暴力集中

這些都不是漏做，而是刻意控制風險。

---

## Warnings status after merge
最終 full pytest：
- `171 passed`
- `61 warnings`

這 61 個 warnings 已確認為同一類問題：
- `ft.Text(style=ft.TextThemeStyle.xxx)` 的 Flet deprecation warning

目前來源集中於：
- `bundler_view`
- `config_view` / `config_form`
- `lookup_view`
- `qc_view`
- `rules_view`

結論：
- 不是本輪 refactor 新引入的功能性錯誤
- 應另外開一顆獨立 UI cleanup PR 處理

---

## Recommended next steps
### A. 短期
- 另外開一顆 UI cleanup PR，專修 61 個 Flet warnings
- 不混進本輪 refactor 主線

### B. 中期
- 若要繼續清技術債，可考慮主題化處理：
  1. QC 線重構 / 保留策略
  2. tkinter / headless CI 相容性
  3. config compat seam 最終 migration

### C. 不建議
- 不建議再用「一路長鏈 PR」模式繼續塞 unrelated cleanup
- 本輪主線已經完整，後面應拆成小主題獨立前進

---

## Final state
- main: `8c61917`
- full pytest: `171 passed, 61 warnings`
- PR40–58 主線：**完成並封箱**
