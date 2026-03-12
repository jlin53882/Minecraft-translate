# PR47 設計稿：`translation_tool/plugins/shared` 共用規則收斂

## Summary
PR47 不做大型搬家，而是把前面幾顆 pipeline 重構後已經看清楚的共用規則正式收回 `translation_tool/plugins/shared/`。成功標準不是 shared 變得很大，而是 plugin 之間不再各自藏一份 path/json/text helper 變體。

---

## Phase 0 盤點
- 目前 `translation_tool/plugins/shared/` 已存在 `json_io.py`、`lang_path_rules.py`、`lang_text_rules.py`，且有 `tests/test_plugins_shared_helpers.py` 基礎保護。
- FTB、KubeJS、MD plugin 都已部分依賴 shared，但 pending traversal、dry-run stats、某些 rename rule 仍可能散落在 pipeline/core 層。
- shared 現況是『有了，但還沒成為唯一真相來源』。
- 若 PR47 不做，後面 PR43~46 拆出的 helper 很容易各自長出平行版本。

---

## 設計範圍
- 補一輪全 repo inventory，盤點 FTB / KubeJS / MD 哪些 helper 仍重複：path rename、already-zh 判定、JSON read/write、pending file traversal、dry-run stats。
- 需要時新增 `translation_tool/plugins/shared/pending_stats.py`，集中 pending traversal / stats 計算；避免再被塞回 pipeline core。
- 對 shared public API 做明確 `__all__` 管理，讓 plugin caller 改依賴 shared module，而不是各抓私有 helper。
- 把舊 helper 改成薄 wrapper 或直接 caller migration；真正刪除舊相容層留給 PR57。
- 測試拆成 `tests/test_plugins_shared_lang_rules.py`、`tests/test_plugins_shared_json_io.py`，保留既有 `tests/test_plugins_shared_helpers.py` 作 baseline。

---

## Validation checklist
- [ ] `rg -n "translation_tool\.plugins\.shared|is_already_zh|compute_output_path|read_json_dict|write_json_dict|collect_json_files" translation_tool tests --glob "*.py"`
- [ ] `uv run pytest -q tests/test_plugins_shared_helpers.py tests/test_plugins_shared_lang_rules.py tests/test_plugins_shared_json_io.py --basetemp=.pytest-tmp\pr47 -o cache_dir=.pytest-cache\pr47`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr47-full -o cache_dir=.pytest-cache\pr47-full`

---

## Rejected approaches
1) 試過：把所有 plugin helper 無差別往 shared 搬，先搬再說。
2) 為什麼放棄：這種『先集中再整理』很容易把 shared 變成新神檔，而且會把 pipeline-specific 邏輯錯搬成 global rule。
3) 最終改採：只收真正跨 plugin 重複且已被前面 PR 驗證穩定的規則，其餘留在各自 pipeline。

---

## Not included in this PR
- 不動 UI。
- 不改 translation tool core 主入口。
- 不在這顆 PR 做 dead code 實刪。

---

## Next step
- PR48 開始整理 app service lifecycle，讓 UI service wrapper 也跟著瘦下來。
