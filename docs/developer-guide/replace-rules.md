# 預設替換規則（`replace_rules.json`）

專案根目錄的 `replace_rules.json` 是簡中經 `s2twp` 轉繁之後，再替換成台灣官方 Minecraft 用語的規則表，
每條規則是 `{"from", "to"}`。檔案格式為 `json.dumps(indent=2, ensure_ascii=False)` 加結尾換行，規則依維護需要排列，
引擎載入時會自行依原詞長度排序，檔內順序不影響結果。

## 套用語意

套用路徑是 `translation_tool/utils/text_processor.py` 的 `safe_convert_text`（CJK 片段 s2twp），再交給
`translation_tool/utils/replace_rules.py` 的 `apply_replace_rules`：

- 字面規則只對轉換後的文字掃描一次，**最長匹配優先**，替換結果不會被其他規則再次替換。
- 所以 `from` 必須是「簡體輸入經 s2twp 之後」會出現的字串；寫成 s2twp 不會產生的字形，規則就永遠不會命中。
  含「岩」的詞要注意：詞組內 s2twp 會保留「岩」（花崗岩），單字「岩」則會變「巖」，以 `tools/audit_replace_rules.py` 的
  `unreachable_from` 檢查為準。
- 目的詞若恰好等於另一條規則的原詞，編譯時會被收斂成那條規則的目的詞（`紅磚塊→紅磚` 加上 `紅磚→紅磚頭` 實際輸出「紅磚頭」）。
  這類同詞異譯無法靠文字區分，列在 `tests/test_default_replace_rules.py` 的允許清單並註明原因。
- 目的詞內含其他規則原詞（交叉引用，例如 `噴濺型迅捷藥水→飛濺迅捷藥水` 與 `迅捷→加速`）在單次掃描下是合法的：
  長詞先匹配，目的詞受保護。判斷依據是官方用語，不是規則看起來是否乾淨。
- `from`＝`to` 的保護型規則用來擋住較短規則（例如 `迅捷藥水→迅捷藥水` 擋住 `迅捷→加速`）。

## 撰寫原則

1. 一律以官方 `assets/official_locales/minecraft/zh_cn.json` → `zh_tw.json` 為準；模組 JAR 自帶譯文只用來檢查是否傷害複合詞。
2. 譯詞不加多餘空白（官方譯名本身含空白者除外，見測試允許清單）。
3. 木材前綴詞（`深色橡木→黑橡木`、`去皮橡木→剝皮橡木`）不加「塊」，否則會套到「深色橡木椅子」之類的複合詞；
   官方單獨名稱需要「塊」時用整名規則。
4. 新增規則要是名詞性詞彙或完整官方名稱，不要放含助詞（了、的、在、是…）的句子片段。
5. 同一個詞短規則已涵蓋時，不要再為每個變體寫一條長規則。

## 審查工具

```bash
python tools/audit_replace_rules.py                          # 官方語料指標 + 結構檢查
python tools/audit_replace_rules.py --mod-db <moddb.db>      # 加上模組語料（Mod 資料庫 1.20.1 的 JAR 自帶 zh_cn／zh_tw）
python tools/audit_replace_rules.py --reference old.json     # 相對另一份規則檔，各語料變好／變差的 key 數
python tools/audit_replace_rules.py --per-rule --mod-db <db> # 逐條規則好壞與未命中規則（較慢）
```

輸出包含：只做 s2twp 與套用規則後的完全一致率（全部 key、名稱類 key）與平均相似度；結構檢查
（`edge_whitespace`、`inner_space_in_to`、`unreachable_from`、`output_differs_from_to`、`duplicate_from`、`conflicts`）。
結構中的重複、前後空白與交叉引用由 `translation_tool/utils/replace_rules_diagnostics.py` 的 `diagnose_rules` 偵測，
規則頁也用同一份診斷。

加入新規則的驗證方式：同時在官方與模組語料回測，官方不得有任何 key 變差；模組變差的 key 要逐一確認是不是
JAR 譯文本身與官方用語不一致（以官方為準）。

## 回歸測試

`tests/test_default_replace_rules.py` 檢查：沒有前後空白與多餘空白、原詞可達、沒有重複原詞、
沒有未定案的交叉引用與輸出漂移（允許清單需寫明原因），以及官方名稱類 key 的完全一致率不得低於固定門檻。
改善規則後，請把門檻調高到新結果向下取整。
