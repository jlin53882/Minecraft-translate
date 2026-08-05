# Minecraft 翻譯工作流 SOP

## 第0步：翻譯工具鏈內部結構

```
lm_translator_main.py (入口調度)
 ├─ lm_translator_shared_loop.py (批次迴圈)
 │   ├─ lm_translator_shared_cache.py (cache 查詢)
 │   └─ lm_translator_shared_preview.py (預覽)
 ├─ lm_api_client.py (Gemini API 呼叫)
 └─ lm_response_parser.py (回應解析)
```

說明：
- **`translate_batch_smart()`** 是對外主入口，依 profile（`lang` / `ftb` / `kubejs` / `md` / `patch`）選用不同 System Prompt 與批次大小上限
- **`lm_translator_shared_loop.translate_items_with_cache_loop()`** 執行實際批次迴圈，內部呼叫 cache 查詢與 API
- **`lm_api_client.call_gemini_requests()`** 發送請求，自動處理 503 Overload / 429 Quota 錯誤並換 Key 重試
- **`lm_response_parser.safe_json_loads()`** 解析 AI 回應，處理 JSON 截斷與 chunked 格式

## 流程總覽

翻譯工作流分為四個主要階段：

1. **JAR Extract** — 從 mod JAR 抽出語言檔（lang / Patchouli / FTB / KubeJS）
2. **Translate** — 將待翻譯內容批次送入 LLM，產生翻譯結果
3. **Merge** — 將翻譯結果與現有繁體中文合併，產出待翻譯清單
4. **Writeback** — 將合併後的檔案寫回輸出目錄

## 各階段說明

### 1. JAR Extract

- **輸入**：mods 資料夾（內含多個 .jar）
- **輸出**：按語言分類的 lang 檔與書籍檔
- **資料格式**：JAR 內含標準 `assets/<modid>/lang/*.json`（Minecraft lang 格式），
  以及 Patchouli 書籍的 `data/pack.mcmeta` / `book.json`；輸出為相同目錄結構的 JSON 檔案。
- **關鍵程式碼**：`translation_tool/core/jar_processor.py`（入口） + `jar_processor_extract.py`（實作）
- **觸發方式**：
  - UI：`ExtractorView` 頁面，選擇「Extract Lang」或「Extract Book」
  - CLI：直接呼叫 `extract_lang_files_generator(mods_dir, output_dir)` 或 `extract_book_files_generator`

JAR 內的路徑會依正規表達式（`lang_codes` 設定值）篩選，只取出 `en_us`、`zh_tw`、`zh_cn` 對應的語言檔。

---

### 2. Translate

- **入口**：`translation_tool/core/lm_translator_main.py` → `translate_batch_smart()`（新主入口）
- **內部委託**：`translate_batch_smart()` 驗證輸入後，委託 `_execute_translation()` 執行，
  `_execute_translation()` 再呼叫 `translate_batch_smart_old()`（舊版實作），
  完成翻譯後由 `_process_output()` 包裝輸出。
  兩者回傳格式相同：`List[dict] + status`，可互換使用。
- **資料格式**：
  - 傳入 `batch_items` 為 `List[dict]`，每項需含 `text`（原文）與 `path`（檔案路徑），
    選填 `cache_type`（預設 `patchouli`）。
  - 回傳 `List[dict]`，每項 `text` 欄位為譯後文字，`status` 為 `"AUTO"` / `"DRY_RUN"` 等。
- **流程**：以 profile（`lang` / `ftb` / `kubejs` / `md` / `patch`）判斷 System Prompt 與批次大小上限
- **Prompt 模板**：從 `lm_translator` 設定區塊讀取 `lang_system_prompt` / `patchouli_system_prompt`，動態注入
- **錯誤處理**：
  - **503 Overload**：原地等待後重送同一批次（最多 3 次），超過則換 API Key
  - **429 RPM/RPD**：根據 Quota ID 判斷是每分鐘還是每日額度，換 Key 或等待
  - **JSON 截斷**：縮小批次（×0.75），重試
  - **漏翻**：記錄警告，保留原文
- **觸發方式**：
  - UI：`TranslationView` 分頁（FTB Quests / KubeJS Tooltips / Markdown）
  - CLI：直接呼叫 `translate_batch_smart()` 或 `translate_batch_smart_old()`

---

### 3. Merge

- **入口**：`translation_tool/core/lang_merger.py` → `merge_zhcn_to_zhtw_from_zip()`
- **資料格式**：輸入為 ZIP 內含 `lang/*.json`（Minecraft lang 格式）與 Patchouli JSON；
  FTB Quests 為 `.snbt`（Stringified Named Binary Tag）格式；KubeJS 為 `.js` 指令稿。
- **合併策略**：
  - 掃描 ZIP 內每個 mod 的 `zh_cn` / `zh_tw` / `en_us` lang 檔
  - 若有 `zh_tw`，以 `zh_tw` 為 base，將 `en_us` 缺失鍵補入
  - 若無 `zh_tw`，以 `zh_cn` 為 base 轉繁體，再補 `en_us` 缺失鍵
  - **差異**（`en_us` 有但 `zh_cn` / `zh_tw` 沒有的鍵）→ 寫入「待翻譯」資料夾
- **衝突處理**：zh_tw 原文優先；差異鍵全部進入待翻譯，不覆蓋現有翻譯
- **輸出結構**：
  - `lang_output/`：合併後的 lang（含待翻譯子資料夾）
  - `patchouli_output/`：Patchouli 書籍內容
  - `other_output/`：手冊、book.json 等其他檔案
- **觸發方式**：
  - UI：`MergeView` 頁面，選擇 ZIP → 設定輸出資料夾 → 點擊「開始合併 ZIP」
  - CLI：呼叫 `merge_zhcn_to_zhtw_from_zip()`

---

### 4. Writeback

Writeback 沒有獨立的階段，而是鑲嵌在 Merge 流程中的最後一步。

合併完成後，`lang_output/`、`patchouli_output/`、`other_output/` 三個子目錄的內容已直接寫入使用者指定的輸出資料夾，無需額外操作。

若要將翻譯結果寫回原始 JAR，則由使用者在 Extraction 階段指定「翻譯後重新封裝」的選項。

---

## 資料流圖（文字版）

```
JAR → [Extract] → lang files → [Translate] → translated files
                                              ↓
 ZIP ─────────────→ [Merge] → merged files → [Writeback] → output/
```

---

## 常見問題

**Q: 翻譯失敗怎麼辦？**
→ 檢查 API Key 配額（429 / 503）、確認 config 中模型已啟用、嘗試縮小批次大小設定。

**Q: Merge 衝突怎麼辦？**
→ Merge 設計為不覆蓋現有 zh_tw，翻譯差異鍵會進入「待翻譯」資料夾，需人工處理。

**Q: ZIP 處理失敗（BadZipFile）？**
→ 確認選擇的是有效 ZIP 檔案，非 JAR 格式。ZIP 會自動剝離單層頂層資料夾前綴。

**Q: 漏翻（數量不符）怎麼辦？**
→ 系統會記錄漏翻的 ID 與路徑，可查日誌手動補翻，或重新翻該批次。
