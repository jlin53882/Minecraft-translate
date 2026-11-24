# Oracle Index 書籍的路徑契約

Oracle Index（模組 id `oracle_index`）把模組的遊戲內文件放在 `assets/oracle_index/books/<書>/`：頁面是 `.mdx`，每個資料夾的選單名稱放在 `_meta.json`。
路徑規則集中在 `translation_tool/core/oracle_index.py`（純函式，不讀檔案、不翻譯、不寫檔），測試在 `tests/test_oracle_index.py`。
掃描與配對已接上：`book_formats.py` 把內容與文件區的 `.mdx` 頁面視為可翻譯的指南（`resolve_book_format`、`is_unsupported_format`），`_meta.json` 的選單名稱也進同一個指南流程；素材與 `data/` 仍不進通用流程。
翻譯與輸出走 jar 提取的指南流程（`find_guide_files`、`load_guide`、`render_guide`）；資料夾式的 Markdown 流水線（`md_pipeline`）只處理 `.md`，不處理 Oracle 書。

契約依據 Oracle Index 1.4.1，以反組譯確認；整合包內會用到它的書是 Oritech（新版版面）與 mffs（舊版版面）。

## 遊戲怎麼讀譯文

1. 讀 `books/<書>/sinytra-wiki.json`：`schema` 恰好是 `"1"` 用新版，其餘（含沒有這個檔、沒有這個欄位）一律舊版。`layout_from_wiki_meta` 做這個判斷。
2. 語系用遊戲目前的語言碼（例如 `zh_tw`）精確比對，不會退回 `zh_cn`；語言碼以 `en_` 開頭時不找譯文。
3. 譯文路徑是對頁面路徑做字串替換：`books/<書>` → `books/<書>/translated/<語系>`（舊版為 `.translated`）。頁面與 `_meta.json` 走同一條規則。`translated_path` 產生這個路徑。
4. 找到譯文檔就整份取代原檔，沒有合併；找不到才讀原檔。

## 版面

| 位置 | 新版（schema 為 `"1"`） | 舊版 |
|---|---|---|
| 內容模式 | `content/` | `.content/` |
| 文件模式 | `docs/` | 書根目錄（其餘非隱藏項目） |
| 譯文 | `translated/<語系>/` | `.translated/<語系>/` |
| 素材 | `assets/` | `.assets/` |
| 資料（配方、屬性） | `data/` | `.data/` |
| 版面宣告 | `sinytra-wiki.json` | `sinytra-wiki.json` |

`content/`、`docs/` 底下與書根目錄的頁面是英文原文，沒有語系記號；其他語言只出現在 `translated/<語系>/` 底下。
`parse_member` 回傳每個成員的位置、語系與 `source_rel`（去掉語系資料夾後的原文相對路徑）。原文與各語系譯文的 `neutral_key` 相同，用它配對。

## 掃描與配對

- 譯文頁的語系由路徑判斷：`text_sniffer.split_locale` 先呼叫 `split_translated`，路徑含 `translated/<語系>/`（舊版 `.translated`）時回傳語系與去掉語系資料夾的中性路徑，原文頁與譯文頁因此得到同一個中性路徑。
- `translated` 前面至少要有一層書的資料夾；語系碼不是 `en_us`、`zh_tw`、`zh_cn` 的譯文歸為其他語言，不處理。
- 掃描 jar 與掃描已解壓資料夾時讀不到 `sinytra-wiki.json`，用 `infer_layout` 只看路徑推測版面；遊戲的實際判斷（`layout_from_wiki_meta`）留給輸出階段使用。
- `_kind_of`（`sniff_groups.py`）對 Oracle 頁面不再靠內容判斷，路徑確認是頁面就是指南。
- `.mdx` 頁面 front matter 最上層的 `title:` 與內文段落一起抽取，標題區塊排在所有段落之後（`guide_blocks.extract_guide_blocks`）；其他副檔名仍只抽 `navigation.title`。

## 選單（`_meta.json`）

- `extract_meta_entries` 依檔案順序取出顯示名稱：字串值的路徑是鍵，物件值（含 `name`）的路徑是 `鍵/name`；空字串與 `icon` 等其他欄位不收。
- 掃描時（`scanner._MetaBlocks`）譯文依路徑對上原文，譯文缺的鍵填空字串，不像一般指南那樣要求區塊數相同。條目鍵是 `books/<書>/<原文相對路徑>#<序號>`。
- 輸出時必須保留原檔的全部鍵，空字串值維持空字串。

## 翻譯輸出

- 找檔：`find_guide_files` 除了 `.md`／`.mdx`／`.txt`，也收 Oracle 書內容或文件區的原文 `_meta.json`；譯文頁與其他語言不收。
- 條目身分：模組 `oracle_index`、鍵 `books/<書>/<原文相對路徑>#<序號>`，與掃描入庫的條目相同。
- 輸出路徑：`guide_locale_rel` 對 Oracle 原文頁與選單回傳 `translated_rel` 的結果，也就是 `books/<書>/translated/zh_tw/…`（舊版 `.translated/zh_tw/…`）；要求原文語系時回傳原路徑。提取資料夾的外層包裝資料夾保留。輸出階段讀不到 `sinytra-wiki.json`，版面用 `infer_layout`。
- 頁面：依一般指南的區塊組回，`title:` 行依行號寫回。
- 選單：`apply_meta_translations` 依 `extract_meta_entries` 的順序把譯文放回原 JSON，所有鍵、順序與 `icon` 等其他欄位原樣保留，空字串值維持空字串；區塊都沒變時輸出原檔文字。
- 打包：`translated/zh_tw/` 底下的檔案不帶原文語系記號，打包照一般檔案收錄；提取到的 `translated/zh_cn/` 譯文會被排除。

## 哪些要翻、哪些不能動

- 要翻：內容與文件區的 `.mdx` 頁面與 `_meta.json`（`is_translatable_source`）。
- 不翻：素材、`data/`（配方與屬性資料）、`sinytra-wiki.json`、書根目錄之外的其他檔案。
- `.mdx` front matter 的 `id`、`ref`、`related_items`、`unlock` 是識別字：連結解析、物品對應與進度解鎖都用它們，不能翻譯。`type` 在 1.4.1 沒有被讀取。
- 內文連結的寫法是「方括號顯示文字加小括號 `@模組:物品` 目標」，只翻顯示文字，`@` 之後的目標不動。
- 譯文頁的 front matter 不會被索引（路徑含 `/translated` 一律略過），生效的是原文頁的欄位。

## 對輸出的要求

- 頁面標題取自 front matter 的 `title`；有譯文頁就以譯文頁為準。譯文頁沒有 `title` 時，遊戲只退回譯文頁內文的第一個標題，不會回頭讀原文頁的 `title`。
- `_meta.json` 是「鍵 → 顯示名稱」。值可以是字串，也可以是含 `name` 的物件。譯文版取代原檔，所以譯文版必須保留原檔的**全部鍵**，缺少的項目選單不會列出。
- `_meta.json` 的值是空字串時，選單文字取該頁的標題；這類值保留為空字串，不要填入翻譯。
- 選單項目的鍵（檔名或資料夾名）不能翻譯。

## 驗證狀態

| 項目 | 狀態 |
|---|---|
| 版面判斷、`translated/` 與 `.translated/` 的替換規則、整份取代、略過譯文頁索引 | 由反組譯確認 |
| 新版 `translated/<語系>/` 結構 | 與 Oritech jar 內的實際結構一致 |
| 舊版 `.translated/<語系>/` 路徑 | 由反組譯推得；整合包內沒有舊版書附譯文，未實機驗證 |
| 舊版書根目錄的頁面歸為文件模式 | 由反組譯推論 |
| `schema` 寫成數字 `1` 視為新版 | 由 Gson 行為推論 |
| `type` 欄位未被讀取 | 由找不到使用處推論 |
| 提取 Oritech／mffs 後的翻譯輸出：頁面與選單組回、輸出路徑、選單保留全部鍵（31 個選單檔、1,198 個頁面區塊） | 已用真實 jar 驗證 |
| 掃描 Oritech／mffs 的 jar：原文頁與 `translated/<語系>/` 譯文頁配對、`title:` 與 `_meta.json` 名稱抽出（Oritech 186 筆、zh_cn 對上 182 筆；mffs 15 筆） | 已用真實 jar 驗證 |
| 遊戲內實際顯示 `translated/zh_tw/` 的選單與頁面 | 未實機驗證 |

## 更新 Oracle Index 時的重新驗證

1. 重新反組譯版面判斷、譯文路徑替換與譯文頁索引，確認三者的行為與上面一致。
2. 重新確認選單與標題的讀取規則（整份取代、空值退回標題）。
3. 用新版的 Oracle Index 與實際的書各開一次遊戲內文件，確認譯文版被載入。
