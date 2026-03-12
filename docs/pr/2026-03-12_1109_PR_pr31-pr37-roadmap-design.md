# PR31~PR37 設計總覽（non-UI，修訂版）

> 目標：只處理非 UI 結構與可維護性，不改畫面與互動流程。  
> Gate：每顆 PR 都必須先完成 **Phase 0 盤點**，再進 Phase 1。

## 執行順序（確認版）
1. **PR31** plugin shared helper 抽離（FTB/KubeJS）
2. **PR32** lang text detection helper 抽離（FTB/MD）
3. **PR33** pipeline logging bootstrap 去重
4. **PR34** non-UI guard tests 擴充（重構前安全網）
5. **PR35** `lm_translator_main.py` Phase 1 切分
6. **PR36** `lang_merger.py` Phase 1 切分
7. **PR37** `cache_manager` 薄 façade（Phase 1）

## 為什麼這順序正確
- PR31~33：先清重複與一致化，降低後續重構噪音。
- PR34：先補安全網，避免 PR35~37 大檔切分時無法判定回歸。
- PR35~37：最後才動核心大檔，風險可控。

## 共通硬規則
- 不改 UI。
- 不同一顆 PR 混「行為改動 + 結構改動」。
- 每顆 PR 必須有：
  - `## Phase 0 盤點`
  - `## Validation checklist`
  - `## Rejected approaches`
- 凡刪除/替換，必寫清楚：
  - 刪除原因
  - 現況 caller
  - 替代路徑
  - 風險
  - 驗證依據
