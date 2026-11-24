# Modonomicon 書籍內的字面英文

Modonomicon 把書籍 JSON 的每個文字欄位當成語系鍵交給遊戲查詢；查不到鍵時，畫面顯示的就是欄位值本身。
書籍作者直接把英文寫進 JSON（例如 Neo Vitae 的條目標題與說明）時，語系檔只要有一筆「鍵 = 那句英文」的條目，遊戲就會顯示譯文，書籍檔不必修改。

抽取規則集中在 `translation_tool/core/modonomicon_literals.py`（`extract_literals`、`collect_mod_literals`），測試在 `tests/test_modonomicon_literals.py`。

## 規則

- 只看文字欄位（`TEXT_FIELDS`）；`id`、`icon`、`type`、`category`、連結與條件等識別字不碰。
- 值已經是模組 `en_us` 的鍵時是正常的鍵引用，不算字面英文。
- 長得像鍵或資源位置的值（`a.b.c`、`mod:name`）不算。
- 含 `%` 的值不收：部分顯示路徑會經過 `String.format`，譯文裡的 `%` 要寫成 `%%`，無法只靠複製字串處理。
- 換行與 `[#]` 等標記屬於字串本身，鍵必須與書籍檔內的字串逐位元相同，所以原樣保留。

## 接上的位置

字面英文只補進模組主語系檔 `assets/<模組>/lang/en_us.json`（`main_lang_mod_id`）：書籍 JSON 在 `data/<模組>/modonomicon/books/` 底下，屬於整個模組。

| 階段 | 位置 | 行為 |
|---|---|---|
| 掃描入庫 | `translation_db/scanner.py` 的 `_with_book_literals` | 語系條目多出「鍵 = 原文」的項目 |
| 提取語系檔 | `core/jar_processor_extract.py` 的 `_with_book_literals_bytes` | 寫出的 `en_us.json` 帶有這些條目；沒有可補的條目時保留原始位元組 |

之後的合併、翻譯與輸出照一般語系條目處理，輸出的 `zh_tw.json` 就會含有以英文為鍵的譯文。

## 限制

- 只處理 `data/<模組>/modonomicon/books/` 底下的 JSON；欄位名稱不在 `TEXT_FIELDS` 的文字不會被找到。
- 遊戲內是否顯示以英文為鍵的譯文，依 Modonomicon 的查詢行為推論，尚未實機驗證。
