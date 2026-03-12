# PR40~58 設計包索引

## Summary
這包是 `Minecraft_translator_flet` 在 PR39B 之後的完整設計稿集合，覆蓋 PR40 ~ PR58。
定位不是願望清單，而是可直接拿去逐顆執行的設計文件骨架。

---

## 這包包含什麼
- 1 份索引文件（本檔）
- 19 份 PR 設計稿（PR40 ~ PR58）
- 每份都補了：
  - `## Phase 0 盤點`
  - `## Validation checklist`
  - `## Rejected approaches`
  - `## Not included in this PR`
  - `## Next step`

---

## 核心排序判斷
1. 先把 non-UI orchestration / pipeline / shared / startup 邊界收乾淨。
2. 再補 view characterization tests，避免 UI refactor 盲飛。
3. 最後才拆大型 view。
4. `qc_view.py` / `app/services.py` 故意留到最後，因為它本質上是產品決策題，不只是技術題。

---

## 檔案清單
- `2026-03-12_1505_PR_pr40-lm-translator-orchestration-split-design.md`
- `2026-03-12_1505_PR_pr41-lm-translator-shared-boundary-design.md`
- `2026-03-12_1505_PR_pr42-lang-merge-content-split-design.md`
- `2026-03-12_1505_PR_pr43-ftb-translator-split-design.md`
- `2026-03-12_1505_PR_pr44-kubejs-translator-split-design.md`
- `2026-03-12_1505_PR_pr45-md-translation-assembly-design.md`
- `2026-03-12_1505_PR_pr46-jar-processor-split-design.md`
- `2026-03-12_1505_PR_pr47-plugins-shared-convergence-design.md`
- `2026-03-12_1505_PR_pr48-services-impl-task-runner-lifecycle-design.md`
- `2026-03-12_1505_PR_pr49-main-entrypoint-boundary-design.md`
- `2026-03-12_1505_PR_pr50-config-proxy-and-text-processor-design.md`
- `2026-03-12_1505_PR_pr51-large-view-characterization-tests-design.md`
- `2026-03-12_1505_PR_pr52-small-view-characterization-tests-design.md`
- `2026-03-12_1505_PR_pr53-cache-view-split-design.md`
- `2026-03-12_1505_PR_pr54-extractor-view-split-design.md`
- `2026-03-12_1505_PR_pr55-translation-view-split-design.md`
- `2026-03-12_1505_PR_pr56-rules-and-config-view-design.md`
- `2026-03-12_1505_PR_pr57-dead-code-and-compat-cleanup-design.md`
- `2026-03-12_1505_PR_pr58-qc-view-and-app-services-final-decision-design.md`

---

## 補一句狠話
這輪最容易搞砸的，不是某一支 600 行的大檔；而是『明明還沒補測試，就急著拆 UI』。
PR51 / PR52 不是暖身，是保命。
