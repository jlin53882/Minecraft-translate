# Patchouli 語意分類與清理邊界

本文件是 Patchouli JSON 掃描、翻譯抽取、翻譯 ZIP 匯入與 ModDB 清理共用的
長期維護契約。Patchouli 檔案不是單純的「所有字串都可翻譯」格式；同一份
JSON 可能同時包含玩家可見文字、語言檔鍵值、資源 ID、配方／多方塊結構、版面
座標與由 Java processor 產生的 template 參數。

## 共用分類

`translation_tool/core/patchouli_semantics.py` 的
`iter_patchouli_semantics()` 是唯一的結構分類入口。每個字串會得到分類、原因、
證據、來源 fingerprint、上下文 fingerprint 與 classifier 版本。抽取器與
`translation_tool/translation_db/scanner.py` 只把 `literal_text` 送進翻譯流程。

| 分類 | 意義 | AI／writer | 清理 |
|---|---|---:|---:|
| `literal_text` | 已由頁型、顯示欄位或已解析 template 確認的玩家可見原文 | 可以 | 不刪 |
| `translation_reference` | `book.json` 啟用 `i18n` 後的語言檔鍵值，或無法安全當原文的鍵值 | 不可以 | 不自動刪 |
| `display_nontranslatable` | 空值、已是 CJK、placeholder、純符號／格式資料 | 不可以 | 不自動刪 |
| `structural` | id、resource path、recipe、image、multiblock、layout、processor 等結構 | 不可以 | 僅明確高信心結構可列入規則候選 |
| `unknown` | 缺少頁型、template、include 或 processor 語意，不能安全猜測 | 不可以 | 不自動刪 |

`unknown` 是保守的安全狀態，不等於「應該刪除」。新頁型或 custom component
若沒有可解析的 template context，應先補 context／測試，再放寬分類。

## 上游證據與版本邊界

目前分類規則以 Patchouli 官方格式文件與 26.1 分支原始碼交叉核對；不把
最新分支的實作細節假設成所有 Minecraft 版本都相同。

| 來源 | 可確認的契約 | 本專案採用方式 | 支援／限制 |
|---|---|---|---|
| [Book JSON Format](https://vazkiimods.github.io/Patchouli/docs/reference/book-json/) | `name`、`landing_text` 是書本顯示欄位；`i18n=true` 會讓 category、entry、page title／text 先走語言檔；`book_texture`、`model` 等是資源 ID | 顯示欄位可進 `literal_text`／`translation_reference`；資源欄位保持 `structural` | `i18n` 缺失或不是布林值時不猜測；資源 ID 不送 AI |
| [Default Page Types](https://vazkiimods.github.io/Patchouli/docs/patchouli-basics/page-types/)；26.1 [`PageWithText`](https://raw.githubusercontent.com/VazkiiMods/Patchouli/26.1/Xplat/src/main/java/vazkii/patchouli/client/book/page/abstr/PageWithText.java) | 文字頁的 `text` 是顯示文字；共同頁欄位的 `type`、`anchor`、`advancement`、`flag` 是頁面控制／識別資料 | `text`／`title` 等白名單顯示欄位分類為文字；控制欄位保留為結構 | 只對文件列出的內建欄位作高信心判定；未知頁型走 `unknown` |
| 26.1 [`PageMultiblock`](https://raw.githubusercontent.com/VazkiiMods/Patchouli/26.1/Xplat/src/main/java/vazkii/patchouli/client/book/page/PageMultiblock.java) | `name` 繼承 `PageWithText` 的可見文字；`multiblock_id`、`multiblock`、`enable_visualize` 是結構／行為資料 | 分開處理 `text`／`name` 與 multiblock 子樹；pattern／mapping 只有明確證據才可進 cleanup | 舊 DB 沒有來源 context 時，只保留既有 key path 的高信心 multiblock 規則 |
| 26.1 [`PageCrafting`](https://raw.githubusercontent.com/VazkiiMods/Patchouli/26.1/Xplat/src/main/java/vazkii/patchouli/client/book/page/PageCrafting.java) | recipe、配方輸入輸出與尺寸由頁型消費，並非一般文章文字 | recipe、item、ingredients、座標與 recipe metadata 分類為結構 | 不把配方 ID、ItemStack 或 layout 值送入翻譯 |
| [Templating](https://vazkiimods.github.io/Patchouli/docs/patchouli-basics/templates/)；26.1 [`PageTemplate`](https://raw.githubusercontent.com/VazkiiMods/Patchouli/26.1/Xplat/src/main/java/vazkii/patchouli/client/book/page/PageTemplate.java)、[`BookTemplate`](https://raw.githubusercontent.com/VazkiiMods/Patchouli/26.1/Xplat/src/main/java/vazkii/patchouli/client/book/template/BookTemplate.java)、[`TemplateComponent`](https://raw.githubusercontent.com/VazkiiMods/Patchouli/26.1/Xplat/src/main/java/vazkii/patchouli/client/book/template/TemplateComponent.java) | template 由 component、include、processor 與變數在執行期編譯；`#variable` 可把 entry 欄位接到文字 component；processor／include 可能改變用途 | 只解析已知文字 component 與明確 `#variable`；processor、include、未知 component 或循環解析失敗都保持 `unknown` | 不把 template 原始 JSON 整份當文章；不對自訂 Java processor 建立模組白名單 |

真實版本盤點只找到 `Patchouli-1.20.1-85-FABRIC.jar` 與
`Patchouli-1.21.1-93-NEOFORGE.jar`；目前工作區沒有 26.1／26.2 版本目錄，
因此 26.x 只用上游格式／原始碼作規則交叉核對，沒有宣稱已完成本機 JAR 驗證。
若未來遇到不同版本的頁型或 component，應先增加版本化 fixture／真實 JAR 證據，
再放寬分類器，不直接套用這張表。

## 上下文來源

- 一般解壓縮目錄由 `context_for_path()` 依同一本書的 `book.json` 與
  `en_us/templates/*.json` 建立有界快取。
- JAR／ZIP scanner 在每個 archive 只讀一次相關 `book.json`／template，將
  `PatchouliContext` 傳給同 archive 的所有 entry；不逐筆重掃 template。
- `book.json.i18n=true` 時，顯示欄位中的 `mod.book.key` 會視為
  `translation_reference`，不送進 AI。
- template 的 `patchouli:text`、`patchouli:header`、`patchouli:tooltip`
  可解析固定文字與 `#variable`；processor、include、未知 custom component
  仍保留為 `unknown` 或 `structural`，不使用末端欄位猜測。
- 找不到來源時，正式 archive 流程使用 `archive_context_missing` 的保守模式；
  只有舊的 in-memory extractor 測試／相容呼叫才保留 document-only sentence
  fallback，不能把這個 fallback 擴大到正式 ZIP 掃描。

## 身分與輸出不變

語意分類不改變既有 `(kind, mc_version, mod_id, file_key#JSON_path)` 身分。
Advancement、Lang、Guide/Markdown 仍由各自的專用 writer／抽取器處理；Patchouli
分類只決定哪些 JSON path 可以進入翻譯與清理候選，不能改寫結構樹、陣列順序或
template 的 processor 參數。

## Minecraft 資源根目錄邊界

Minecraft Java 資源包使用 `assets/<namespace>/...`，資料包使用
`data/<namespace>/...`；兩者不是可以任意互換的通用路徑。Patchouli 的實際書本
可以由 `data` 下的 `book.json` 搭配 `assets` 下同一 namespace／book 的 entries
與 templates 組成，因此 scanner 與目錄翻譯入口只在「同一個書本根」範圍內把兩個
resource root 連到同一份 context。這個 canonicalization 不套用到 Lang、Advancement
或其他資源，writer 仍保留每個輸入檔案原本的相對路徑。

格式參考：[Minecraft Wiki：資源包／目錄結構](https://zh.minecraft.wiki/w/%E8%B5%84%E6%BA%90%E5%8C%85?variant=zh-tw#%E5%85%A8%E5%B1%80%E8%B5%84%E6%BA%90%E5%8C%85)
與 [Minecraft Wiki：命名空間 ID](https://zh.minecraft.wiki/w/%E5%91%BD%E5%90%8D%E7%A9%BA%E9%97%B4ID)。

## 清理規則

有語意結果時，`entry_cleanup_category(..., semantic=...)` 必須遵守同一個分類：

- `unknown`、`translation_reference`、`display_nontranslatable` 永不自動刪除。
- `structural` 只有帶有明確證據才可成為清理候選；多方塊 pattern 仍使用既有
  專用類別與保護／preview／token／CAS／revert 契約。
- 舊資料庫中沒有原始 JSON context 的資料，清理流程只接受資料庫鍵本身能證明的
  高信心多方塊結構；placeholder、resource id、短字與 custom page 一律視為
  unknown，不會自動刪除。新掃描也不會以未知欄位復活結構值；若要放寬舊資料
  清理，必須先加入可回溯的來源 context 或 semantic metadata。
- 由這條保守路徑產生的檢查檔會附上 `semantic_kind`、`semantic_reason`、
  `semantic_evidence`、`source_available` 與 `classifier_version`；舊格式檢查檔
  若不符合目前高信心規則，預覽會拒絕套用並要求重新匯出。

## 必要回歸測試

修改分類器、scanner 或 writer 時至少覆蓋：

1. standard page 的 `text/title` 與 `anchor/recipe/multiblock` 分離。
2. `i18n=true` 的 translation reference 不進 AI；`i18n=false` 的顯示字串不被
   誤當語言鍵。
3. template variable 的 custom page、missing template、processor／include
   與未知 component 保守處理。
4. clean English 與 translated ZIP 使用相同語意 path；translated ZIP 沒有
   en_us 時只能匯入已確認的玩家可見欄位。
5. writer round-trip 後 JSON 結構、陣列順序與非文字欄位完全不變；Lang、Guide、
   Advancement 既有測試不得回歸。

Patchouli 的頁型、template 與 component 定義應以實際使用版本的原始碼／官方
文件核對，不以單一 mod 的 README 推論所有 custom page 行為。
