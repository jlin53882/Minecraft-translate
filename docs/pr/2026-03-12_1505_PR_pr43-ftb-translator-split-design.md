# PR43 設計稿：`translation_tool/core/ftb_translator.py` pipeline 分層

## Summary
PR43 的任務是把 FTB pipeline 從單一流程檔整理成 export / clean / template / orchestration 四層。這顆 PR 只切結構，不改 `run_ftb_pipeline()` 對外契約，也不改 FTB 翻譯策略。

---

## Phase 0 盤點
- 目前 `translation_tool/core/ftb_translator.py` 約 619 行。
- 同一支檔案內包含 raw export、clean、template prepare、LM handoff、pipeline orchestration。
- 目前 repo 沒有 FTB 專屬 focused tests，屬於典型『只能靠 full pytest 撐』的高風險區。
- FTB 與 KubeJS / MD 其實是平行 pipeline；若這顆不先切乾淨，PR47 的 shared 收斂會缺基準。

---

## 設計範圍
- 新增 `translation_tool/core/ftb_translator_export.py`，承接 raw export 與 quests root resolve。
- 新增 `translation_tool/core/ftb_translator_clean.py`，承接 clean / prune / deep merge helpers。
- 新增 `translation_tool/core/ftb_translator_template.py`，承接 template prepare 與 LM handoff 前的輸入整理。
- `translation_tool/core/ftb_translator.py` 只保留 `run_ftb_pipeline()` 與最少量 orchestration glue。
- 新增 `tests/test_ftb_translator_export.py`、`tests/test_ftb_translator_clean.py`、`tests/test_ftb_pipeline_smoke.py`，先鎖 input/output contract。

---

## Validation checklist
- [ ] `rg -n "def resolve_ftbquests_quests_root|def export_ftbquests_raw_json|def clean_ftbquests_from_raw|def prepare_ftbquests_lang_template_only|def run_ftb_pipeline" translation_tool/core/ftb_translator*.py`
- [ ] `uv run pytest -q tests/test_ftb_translator_export.py tests/test_ftb_translator_clean.py tests/test_ftb_pipeline_smoke.py --basetemp=.pytest-tmp\pr43 -o cache_dir=.pytest-cache\pr43`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr43-full -o cache_dir=.pytest-cache\pr43-full`

---

## Rejected approaches
1) 試過：直接把 FTB pipeline 改成 package 結構，順便統一 KubeJS / MD 命名。
2) 為什麼放棄：命名統一很爽，但一次跨三條 pipeline 會把 PR43 變成 repo-level 規格改造，超出單顆 PR 應承受的風險。
3) 最終改採：先用同前綴 sibling modules 做低風險切分，等三條 pipeline 都穩了再在 PR57 評估命名清理。

---

## Not included in this PR
- 不改 FTB 實際翻譯資料內容。
- 不修改 plugin shared 規則。
- 不處理 UI view。

---

## Next step
- PR44 用同樣節奏處理 `kubejs_translator.py`。
