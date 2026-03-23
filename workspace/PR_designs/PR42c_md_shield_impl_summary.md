# PR 42c：Markdown 翻譯流程整合 rich_text_shield 實作摘要

**Branch**：`pr/rich-text-shield`
**日期**：2026-03-23

## 結論
Markdown 翻譯流程 **需要** shield/unshield 保護。

原因不是 Markdown 內容都充滿格式碼，而是 `md_lmtranslator.py` 的翻譯輸入來源仍可能包含：
- Minecraft 色彩碼（例如 `&a`、`&c`）
- 物品 ID / 標記片段（例如 `#minecraft:stone`）
- 其他不應被翻譯引擎改寫的 rich text 片段

因此沿用 `rich_text_shield` 的前處理 / 後處理模式是合理且安全的。

## 實作摘要
1. 在 `md_lmtranslator.py` 中保留 `shield_text()` / `unshield_text()` 的匯入。
2. 對進入翻譯管線的 Markdown 文字先做 shield：
   - 翻譯前：`shield_text(source_text)`
   - 翻譯後：`unshield_text(translated_text, shielded.shields)`
3. 保留原本的 content hash 去重與 cache 流程，不改變 pending schema。
4. `write_json(..., ensure_ascii=False)` 既有行為維持不變。

## 驗證
- `python -m py_compile translation_tool/plugins/md/md_lmtranslator.py`
- `uv run pytest -q`

## 備註
本次 Markdown 管線採取「保守保護」策略：
即使大多數段落是純文章，也先保護可能出現的 rich text 片段，避免翻譯引擎誤改格式。
