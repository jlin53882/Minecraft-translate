# Minecraft Translator v0.7.0

Tag: `v0.7.0`
Title: **Minecraft Translator v0.7.0**

> 範圍：PR10 ~ PR39

## Major features
- **FTB Quest 翻譯進度條驗證報告**：新增 FTB Quest 抽取格式完整性驗證與進度條驗證報告（`step6 progress bar validation report`）。

## Improvements
- **翻譯視圖日誌區**：深色 Container 背景替代 styled_card；ListView 移除無效 bgcolor 參數；初始化提示文字；日誌自動滾到底。
- **KubeJS 翻譯 Pipeline**：OpenCC 簡→繁轉換延伸至 `client_scripts` 來源值；跳過 ASCII 藝術字（█▓▒░）；`skip_chinese=False` 邏輯修正；新增 `item.kubejs.*` 翻譯記憶匹配。
- **翻譯進度**：FTB 進度改以檔案數均勻推進（非 chunk 數）。

## Bug Fixes
- `scroll_to(end=True)` → `scroll_to(offset=1.0)`（Flet 0.28.3 API 相容）。
- `log_presenter._entry_color` 移除錯誤 hex 前綴拼接，正確使用 `Colors` enum 的 `.value`。
- `kubejs_translator_clean.py` 補回 `import json`（`jq` 依賴）。
- CI：修復 lint F401（`threading` import 移除又恢復）；Ruff format 6 個檔案。

## Tests
- 3 個 `test_translation_view_characterization.py` characterization tests 新增（翻譯視圖行為迴歸保護）。

---

## What's Changed
完整變更清單：https://github.com/jlin53882/Minecraft-translate/compare/v0.6.0...v0.7.0
