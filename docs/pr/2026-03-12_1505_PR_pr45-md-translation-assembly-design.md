# PR45 設計稿：`translation_tool/core/md_translation_assembly.py` 步驟與進度層整理

## Summary
PR45 的重點不是再拆功能，而是把 Markdown pipeline 的 step glue、統計、進度代理分清楚。這顆 PR 成功的樣子，是 `run_md_pipeline()` 保留原契約，但 step1/2/3 與 progress proxy 可以被單獨測。

---

## Phase 0 盤點
- 目前 `translation_tool/core/md_translation_assembly.py` 約 488 行。
- 檔案內同時有 `_ProgressProxy`、step1/2/3、stats logging、總入口 `run_md_pipeline()`。
- repo 目前沒有 MD pipeline 專屬 focused tests。
- 這顆檔案比 FTB / KubeJS 小，但抽象層混裝同樣明顯：UI/progress 代理與 domain step 邏輯黏在一起。

---

## 設計範圍
- 新增 `translation_tool/core/md_translation_progress.py`，集中 `_ProgressProxy` 與 UI-facing progress adapter。
- 新增 `translation_tool/core/md_translation_steps.py`，集中 `step1_extract()`、`step2_translate()`、`step3_inject()` 的 step-level logic。
- 新增 `translation_tool/core/md_translation_stats.py`，集中 file counting、pending doc counting、step2 stats logging。
- `md_translation_assembly.py` 退成入口 orchestrator：負責組 config、串 step、yield/update、summary。
- 新增 `tests/test_md_pipeline_steps.py`、`tests/test_md_progress_proxy.py`。

---

## Validation checklist
- [ ] `rg -n "class _ProgressProxy|def step1_extract|def step2_translate|def step3_inject|def run_md_pipeline" translation_tool/core/md_translation*.py`
- [ ] `uv run pytest -q tests/test_md_pipeline_steps.py tests/test_md_progress_proxy.py --basetemp=.pytest-tmp\pr45 -o cache_dir=.pytest-cache\pr45`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr45-full -o cache_dir=.pytest-cache\pr45-full`

---

## Rejected approaches
1) 試過：保留單檔，只靠區塊註解把 progress / stats / steps 隔開。
2) 為什麼放棄：這招對 488 行短期還撐得住，但後續誰都不敢動；可測邊界還是不存在。
3) 最終改採：直接切成 progress / steps / stats 三層，讓後面 shared 收斂有明確接點。

---

## Not included in this PR
- 不改 Markdown 抽取/回寫策略。
- 不調整 log 文案與 UI 呈現。
- 不重寫 plugin 模組。

---

## Next step
- PR46 接續處理 jar 掃描、抽取、預覽、報表分層。
