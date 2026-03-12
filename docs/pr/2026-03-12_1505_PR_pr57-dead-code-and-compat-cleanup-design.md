# PR57 設計稿：dead code / compatibility leftovers 保守清理

## Summary
PR57 不是『大掃除爽刪』，而是一顆很保守的收尾 PR。只有在 PR40~56 的新邊界都站穩後，才允許把舊 wrapper、舊 alias、薄 façade、無 caller helper 安全拿掉。這顆 PR 的關鍵是證據，不是感覺。

---

## Phase 0 盤點
- 前面 PR 會留下不少暫時性 façade / import aggregator / legacy seam，例如 `lm_translator_shared.py` 的薄 façade、舊 helper wrapper、可能殘留的 config proxy caller。
- 這顆 PR 必須建立在前面 focused tests 已到位的前提上，否則 cleanup 很容易刪掉 staging seam。
- repo 現況已有 `tests/test_main_imports.py`、`tests/test_plugins_shared_helpers.py`、cache view tests 等，可作為 cleanup 的 baseline guard。
- 所有『無 caller』判定都必須附搜尋證據，不允許憑感覺刪。

---

## 設計範圍
- 先做 inventory：列出每個準備刪除/替換的 wrapper、alias、compat import、舊 helper。
- 對每個刪除項目逐一補六欄位：為什麼改、為什麼能刪、目前誰在用/沒人在用、替代路徑、風險、驗證方式。
- 優先清掉已被新模組完整取代的 import façade，例如 shared import aggregator、舊 pipeline helper wrapper、legacy config access shim。
- 文檔與模組頂部註解同步收斂，避免代碼清掉了但說明還停在舊世界。

---

## 刪除/移除/替換說明
- 這顆 PR 高機率涉及刪除/替換；PR 文件必須逐項補足六欄位刪除說明，不足處一律標 `[需確認]`。
- 任何聲稱『無 caller』的刪除，都必須附 `rg` 或等價搜尋證據。
- 若某條相容層只是暫時無測試，但仍可能被外部流程使用，不能硬砍；要嘛補證據，要嘛延後。

---

## Validation checklist
- [ ] `rg -n "TODO|Deprecated|legacy|compat|wrapper|shim|re-export|from .* import \*" translation_tool app --glob "*.py"`
- [ ] `rg -n "lm_translator_shared|lang_merge_content|config = LazyConfigProxy|app\.services" translation_tool app tests --glob "*.py"`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr57-full -o cache_dir=.pytest-cache\pr57-full`

---

## Rejected approaches
1) 試過：把所有看起來像舊碼的東西一次砍掉，再靠 full pytest 找屍體。
2) 為什麼放棄：這是最容易把 staging seam、低頻 caller、測不到的 import path 一起炸掉的做法。
3) 最終改採：先 inventory、先證據、逐項刪；寧可慢，也不要賭。

---

## Not included in this PR
- 不做新結構設計。
- 不在這顆 PR 改產品行為。
- 不處理 `qc_view.py` / `app/services.py` 最終命運。

---

## Next step
- PR58 最後才碰 QC 舊線，因為那題不只技術，還有產品決策。
