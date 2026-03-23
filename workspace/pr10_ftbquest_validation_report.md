# FTB Quest 抽取驗證報告

**PR10 分支**：`pr10/translation-view-log-fix-and-ftbquest-extraction`  
**日期**：2026-03-22  
**驗證協調總管**：Task B Sub-agent

---

## 1. 目錄覆蓋率

### 輸入覆蓋
| 類別 | 數量 | 說明 |
|------|------|------|
| 總檔案數 | 2911 | INPUT_DIR 所有檔案 |
| FTB Quest .snbt 檔案 | 706 | `config/ftbquests/quests/` |
| FTB Quest 語系檔案 | 134 | en_us(67) + zh_cn(67) |
| 其他 .snbt 檔案 | 572 | 任務本體（chapters/ 等）|

### 輸出覆蓋
| 類別 | 數量 | 說明 |
|------|------|------|
| 總輸出檔案 | 8 | JSON 格式 |
| 抽取後 TSV（新增） | 2 | `ftbquests/extracted/en_us/zh_cn/` |

**覆蓋率**：FTB Quest 語系檔案 100% 抽取，Quest 本體 0%（見下方建議）

---

## 2. 抽取統計

### 抽取結果

| 語系 | lang keys | quests keys | 總計 | 檔案數 |
|------|-----------|------------|------|--------|
| en_us | 6550 | 0 | **6550** | 67 |
| zh_cn | 6516 | 0 | **6516** | 67 |
| **總計** | **13066** | **0** | **13066** | 134 |

### 與現有輸出比對

| 現有輸出 | keys 數量 | 對應抽取 |
|---------|----------|---------|
| `raw/en_us/ftb_lang.json` | 6550 | ✅ 完全匹配 |
| `raw/zh_cn/ftb_lang.json` | 6516 | ✅ 完全匹配 |
| `待翻譯/en_us/ftb_lang.json` | 40 | 差異：6510 keys 有 zh_cn 譯文（快取命中） |
| `整理後/zh_tw/ftb_lang.json` | 6516 | ✅ 對應 zh_cn 並已轉繁 |

### 重大發現：Quest 本體未抽取
- `ftb_quests.json` 全為 **0 keys**
- 706 個任務本體 .snbt 檔案（chapters/ 等）未產出任何 quests 翻譯 key
- 原因懷疑：`extract_quest_file` 函式的欄位匹配邏輯與實際 SNBT 結構不符

---

## 3. 格式問題

| 檢查項目 | 結果 | 說明 |
|---------|------|------|
| 多行文字未轉義 | ✅ 無 | `\n` 已置換為 `\\n` |
| Tab 污染分隔符 | ✅ 無 | 精確 1 個 Tab |
| 非 UTF-8 字元 | ✅ 無 | 全為 UTF-8 |
| 空 KEY/VALUE | ✅ 無 | 全部有效 |
| 引號不配對 | ✅ 無 | 全部有效 |
| Minecraft 格式碼 | ⚠️ 存在（預期）| `§0-§f`, `&0-§f` 等，需保留 |
| Placeholder | ⚠️ 存在（預期）| `{variable}` 格式，需保留 |
| HTML 不完整標籤 | ✅ 無 | 全部有效 |

**結論：ALL CLEAR（格式無問題，需保留的內容已正確保留）**

---

## 4. 建議行動

### 高優先度
1. **[Critical] 修復 Quest 本體抽取**
   - 706 個 .snbt 任務檔案（chapters/ 等）未產出翻譯 key
   - 需檢查 `extract_quest_file` 的 `title/subtitle/description` 欄位是否存在於實際 SNBT 結構
   - 建議：印出 1-2 個任務 .snbt 的實際結構（DEBUG 模式）比對

2. **[High] 確認抽取 TSV 格式標準**
   - 目前抽取 TSV 為翻譯工具內部格式（非對外 API）
   - 建議明文化：是否需要 TSV 輸出？還是以 JSON 為主？

### 中優先度
3. **[Medium] 翻譯覆蓋率差異**
   - 待翻譯（40 keys）vs 原始（6550 keys）：僅 0.6% 需翻譯
   - 其餘 6510 keys 已有 zh_cn 譯文（快取）
   - 確認：待翻譯的 40 keys 是否確實是新增/差異內容

4. **[Medium] 擴展其他語系**
   - 目前只支援 en_us + zh_cn
   - 12 個語系（es_es, fr_fr, ja_jp, ko_kr...）未被抽取

### 低優先度
5. **[Low] Minecraft 格式碼處理文件化**
   - 確認 `§` 和 `&` 格式碼在翻譯流程中如何被處理
   - 建議在抽取環節即標記「需保留格式碼」

---

## 附錄：抽取工具位置

| 檔案 | 說明 |
|------|------|
| `translation_tool/plugins/ftbquests/ftbquests_snbt_extractor.py` | FTB Quest 抽取器 |
| `translation_tool/plugins/ftbquests/ftbquests_snbt_inject.py` | 翻譯注入器 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py` | FTB Quest AI 翻譯 |
