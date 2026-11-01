# Related Lang 有界原型與品質驗證（第三輪）

## 1. 摘要與判定

- **判定：MORE VALIDATION REQUIRED，不建議整合。**
- 結構配對（`X` ↔ `X.desc/.tooltip/.title/...`）有明顯價值，抽樣判讀大多相關。
- 詞彙線索（Tier B）目前不可用：絕大多數命中是雜訊或同系列變體，不是同一實體。
- 70k 檢索總耗時 444.84 秒（超過 60 秒門檻），依規格判為**性能阻擋**。
- 人工標註未完成，所有 precision 與語意準確率均為**未測**。

## 2. 基準與worktree

- PR #220 已於 2026-11-01 合併（merged），合併時的 head 為 `e0f4b7a7`，不是規格中的 `ce1bf569`（後者是合併前的較早 commit）。本輪以目前 `origin/main`（`4c572234`）為基準，未修改 PR #220 或 main。
- 實驗在獨立 worktree `experiment/related-lang-probe`（`/tmp/claude-0/relprobe`），產物位於 `artifacts/related_lang_probe_20261031/`，產品程式碼未改動。
- **資料庫限制**：規格指定的正式 DB（`C:\\...\\mod_translation.db`，`entry=774,018`）不在此環境，無法存取。本輪改用你上傳的 ATM10 jar 語系內容建立的隔離測試庫 `lang_v2.db`（158,317 筆 `kind=lang`，其中 29,508 筆有 jar 內建 zh_tw）。
- 該測試庫沒有任何人工審核譯文（`review_status` 全為 `none`），因此 reviewed 分層**無法實測**。
- 未取得 `ab4d7931…` sidecar run，`983 needs_context` 的真實清單也無法重建；相關數字不得當作已驗證。

## 3. 方法

- 原型 `prototype.py`：只讀連線（`mode=ro`），只在同 `mod_id + mc_version + kind` 內檢索，最多回傳 5 筆新增 evidence。
  - Tier A：結構後綴配對，走既有索引 `idx_smart_review_context_key`。
  - Tier B：詞彙線索，僅 low 信心；同系列變體（只差尺寸或序號後綴）獨立為 `sibling_variant`，不視為同一實體。
  - 格式不一致（`%s`、`{0}`、`§` 等）的候選降為 low；zh_tw 與英文相同者標記 `untranslated_reference`；unreviewed 永遠不是權威。
- 停用詞：泛用詞與狀態詞（`item`、`tooltip`、`connecting`、`off` 等）不建立連結；略過鍵的前兩段（類型與命名空間）。

## 4. 實測數據（隔離測試庫，單機、熱快取）

| 指標 | 1,000 筆（pass 1） | 1,000 筆（pass 2） | 70,000 筆 |
|---|---:|---:|---:|
| 總耗時 | 6.68 s | 6.47 s | 444.84 s |
| p50 | 4.82 ms | 4.25 ms | 4.21 ms |
| p95 | 19.42 ms | 19.68 ms | 18.38 ms |
| p99 | 25.1 ms | 24.01 ms | 24.59 ms |
| 最大 | 29.51 ms | 29.37 ms | 192.59 ms |
| 零候選目標 | 56 | 38 | 3663 |
| 平均 / 最多每目標 | 4.52 / 5 | 4.62 / 5 | 4.55 / 5 |

- 峰值 RSS：32.2 MB（整個 70k 壓測程序）。
- 70k 測試為 70,000 個不同目標在 158k 條目的資料庫上連續檢索，**不是** 70k 條目的資料庫；沒有建立索引，沒有另外計時 preparation、index build 或 context assembly，也沒有 UI thread 量測。
- 量測前後資料庫檔案大小與 mtime 均未變（唯讀確認）。

**候選分布（70k）**：結構 medium 6179、結構 low 1602、詞彙 low 308308、同系列變體 2565；標記為未翻譯參考的 5677。有結構配對的目標約 11.1%。

## 5. 查詢計畫

見 `query_plans.md`。三種查詢都沒有 temp B-tree、沒有全表掃描；但詞彙查詢走 `idx_smart_review_glossary_order`，會讀取整個模組的列再以 LIKE 過濾，LIMIT 200 並不限制掃描量。模組越大，成本越高，這是性能風險。

## 6. 品質判讀（作者自評，不是人工金標）

**重要**：以下判讀由撰寫者（AI）完成，**不得當作 precision 或人工標註**。
- 結構配對抽樣 15 組：約 14 組明確相關（例如 `.desc`／`.tooltip` 對應同一物品、選項或進度），1 組不確定。
- 詞彙命中抽樣 40 組：明確相關約 1–3 組（例如同一寶箱的字幕），術語或系列上的間接相關約 8 組，其餘約 30 組為雜訊。
- 候選的中文若為英文原文（未翻譯）會被標記，不被當作譯文參考。

**成功例**：`block.createaddition.redstone_relay` ↔ `.tooltip`（同一方塊的說明，兩側中文一致）；`advancement.create.glass_pipe` ↔ `.desc`（補充了成就的實際條件）。

**失敗／誤命中例**：
- `chisel` 系列 `red_sand_5x` ↔ `red_sand_1x`：同系列，不同實體（第一版被當成詞彙命中，已改為 `sibling_variant`）。
- `rs_mode_sat_inv` ↔ `rs_mode_field_inv.info`：因泛用詞 `inv`、`sat` 而連結（已加入停用詞）。
- `connecting`／`off` 等狀態詞造成的大量假命中（已加入停用詞）。
- 只用最後一段當 token 時，`item.demo.gear.main` 找不到 `gear`（已修正為使用識別段）。

## 7. 測試

- `test_related_lang_probe.py`：11 項，全部通過（`pytest artifacts/related_lang_probe_20261031/test_related_lang_probe.py`）。涵蓋：`.desc`／title 雙向配對、結構未審核上限為 medium、泛用與短詞不造成連結、跨版本與跨模組不回傳、同系列變體分類、格式不一致降級、5 筆上限與排除既有 evidence、確定性與唯讀（資料庫 hash 不變）、特殊 Unicode 與極長鍵、未知目標、未翻譯參考標記。
- 完整 repo 測試：在實驗 worktree 執行 `python -m pytest tests -q -n auto`，結果 **7944 passed、18 skipped、0 failed**。worktree 與 `origin/main` 相同，未改產品程式碼。
- 未做 3／5／8 敏感度實驗（規格標為可選）。

## 8. 標註資料

- `sample_for_human_labeling.csv`：100 筆（結構 40、僅詞彙／變體 40、零候選 20；固定 seed `20261031`），UTF-8，完整字串，所有標註欄位空白。
- 其中 22 筆帶有 jar 內建的 zh_tw；審核狀態全部為 `none`。
- **未人工標註**，因此 candidate recall、precision@5、直接相關比例、有助消歧比例、真正術語衝突比例全部為**未知**。

## 9. 相容性邊界

- **Schema**：未修改，未建立永久索引，未 migration。
- **Provider／quota**：本輪沒有任何 AI 呼叫，零費用。
- **Sidecar／CAS**：未讀寫 JSON sidecar 或 CAS；原型不接入 Smart Review。
- **48 上限**：`smart_review_context.py` 第 80 行確認為 `[:48]`；原型只提供新增候選，不做合併，因此未驗證合併策略。
- **`lang` 與 `kubejs_lang` 分流**：未完整核對 glossary 分流的所有程式路徑（本輪只驗證了 `kind='lang'` 的檢索）。

## 10. 剩餘證據缺口

1. 人工標註 60–100 筆（未完成）。
2. 正式 DB（774k 條目）上的 precision 與 70k 性能量測（未取得）。
3. reviewed 譯文的分層評估（測試庫無審核譯文）。
4. 詞彙層級的雜訊問題（需要更強的訊號，例如 en_us 原文的實體對齊）。
5. UI 90 ms 目標（未測；本輪無 UI）。
6. 70k 性能：需要索引或分批策略，並分開量測 preparation、index build 與 context assembly。
7. AI A/B（規格第 6 節）：未執行，也不應在人工標註前執行。

## 11. 決策

- **判定：MORE VALIDATION REQUIRED，不建議整合**，理由：
  - precision@5：未測（需要人工標註）。
  - 未審核誤標權威：原型已強制 unreviewed 不為權威，此項通過，但缺乏產品層級測試。
  - 跨版本 high-confidence：測試通過（跨版本不回傳），通過。
  - 格式損壞：原型不修改文字，測試通過。
  - context ≤ 48：原型只回傳 ≤ 5 筆；整合時的 48 上限未驗證。
  - **70k preparation < 60 秒：未通過**（retrieval 本身 444.84 秒，未含其他準備）。
  - 重大 false revision：無 AI 呼叫，未測。
- 若要進入下一輪，優先處理：（a）人工標註 100 筆；（b）詞彙層級的訊號強化或移除；（c）70k 性能的索引設計。
