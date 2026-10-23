# 官方術語解析與單獨成句 allowlist

機翻 prompt 與智慧校對 evidence 共用同一個 resolver：
`OfficialLocaleIndex.official_terminology_matches_for_item`（`translation_tool/core/official_locale.py`）。
兩端只透過 `translation_tool/core/terminology_model.py` 的 adapter 轉成各自輸出，不各自組術語。

## 命中優先順序

| 順序 | `match_type` | 說明 |
|---|---|---|
| 0 | `exact_key` | 來源 key 與英文都與官方記錄一致 |
| 1 | `exact_text` | 整句恰為官方完整翻譯且目標唯一 |
| 2 | `official_phrase`／`standalone_allowlist` | 多詞官方片語；或單獨成句且在 allowlist 內的單詞 |
| 3 | `source_substring` | 長文中的官方單詞／複合詞拆出的單詞（詞界匹配） |

同一來源出現兩個互相衝突的目標時整筆排除（`exact_key` 例外）；較長片語涵蓋的較短詞不重複送出；每筆最多 6 個術語。

## 單獨成句為什麼預設不命中

官方複合詞（例如 `Max Absorption`、`Acacia Wood`、`Acacia Planks`）可推導出單詞，但推導結果常是片段：
`Max`→最大傷害值、`Wood`→塊、`Planks`→材。來源整句恰為這類詞時，resolver 不套用子字串／複合詞術語，
避免把片段當成權威譯名送給模型。`Blocker` 這類只是包含 `Block` 字母的詞，由詞界規則排除。

## 單獨成句 allowlist

`terminology_model.STANDALONE_ALLOWLIST`（`{正規化英文: 繁中目標}`）是人工審核過的子集。單獨成句時只有同時滿足下列條件才命中，
`match_type` 與 `usage` 都是 `standalone_allowlist`，並保留官方推導來源 key 作為 provenance：

1. 詞在 allowlist 內。
2. 目前官方語系的複合詞推導目標與 allowlist 目標一致（語系更新造成漂移時不放行）。
3. 官方語系沒有整句恰為該詞的完整條目（有的話由 `exact_text` 處理）。

### 新增 allowlist 項目的審核方式

1. 執行 `python -m tools.report_standalone_term_candidates candidates`，取得候選：來源、目標、推導 key、出現次數、旗標。
2. 只考慮名詞性材質／物件，且目標不是單字片段（旗標含 `single_char_target` 者需逐一審核，`Wood`、`Planks` 這類片段不放行）。
   動詞、屬性前綴（`Max`、`Step`）、形容詞（`Wooden`）、泛用 UI 詞（`Block`）不放行。
3. 用官方語系確認詞單獨使用時譯名唯一、沒有同名完整條目。
4. 在 `tests/data/standalone_term_fixture.json` 加上期望目標，並用
   `python -m tools.report_standalone_term_candidates effect --fixture tests/data/standalone_term_fixture.json`
   確認新增誤匹配為 0。
5. 在 `tests/test_official_standalone_allowlist.py` 補命中案例；危險詞維持「不命中」測試。

不可直接移除 `official_terminology_matches_for_item` 中對「單獨成句的子字串／複合詞」的略過。

## Mod DB 受益統計（唯讀）

`python -m tools.report_standalone_term_candidates moddb --db <translations.db>` 以唯讀連線統計
單獨成句條目總數、有效未審核數、allowlist 命中數與依模組／術語分布，不寫入資料庫。
