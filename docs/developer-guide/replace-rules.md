# 預設替換規則（`replace_rules.json`）

專案根目錄的 `replace_rules.json` 是簡中經 `s2twp` 轉繁之後，再替換成台灣官方 Minecraft 用語的規則表，
每條規則是 `{"from", "to"}`。檔案格式為 `json.dumps(indent=2, ensure_ascii=False)` 加結尾換行，規則依維護需要排列，
引擎載入時會自行依原詞長度排序，檔內順序不影響結果。

## 套用語意

套用路徑是 `translation_tool/utils/text_processor.py` 的 `safe_convert_text`（CJK 片段 s2twp），再交給
`translation_tool/utils/replace_rules.py` 的 `apply_replace_rules`。規則有三種：

- **整段完全相符規則**：`from` 寫成 `^文字$`（中間不含正則符號，判斷見 `exact_source`）。只在整段文字（例如 lang 檔的一個值）
  剛好等於這段文字時套用，先於其他規則，結果不再被替換。用來處理單獨名稱與複合詞譯法不同的詞：
  `^紅樹木$→紅樹林木塊`（`block.minecraft.mangrove_wood`）與 `紅樹木→紅樹林木`（「紅樹木椅子」→「紅樹林木椅子」）並存。
- **字面規則**：只對轉換後的文字掃描一次，**最長匹配優先**，替換結果不會被其他規則再次替換。
- **正則規則**：`from` 含 `.?*[]()\` 時視為正則，在字面規則之後依序套用。

字面規則的注意事項：

- `from` 必須是「簡體輸入經 s2twp 之後」會出現的字串；寫成 s2twp 不會產生的字形，規則就永遠不會命中。
  含「岩」的詞要注意：從簡體轉換時詞組會保留「岩」（岩浆怪→岩漿怪），單字「岩」則會變「巖」，以 `tools/audit_replace_rules.py` 的
  `unreachable_from` 檢查為準。
- 目的詞若恰好等於另一條規則的原詞，編譯時會被收斂成那條規則的目的詞。
- 目的詞內含其他規則原詞（交叉引用，例如 `去皮緋紅菌核→剝皮緋紅菌絲體` 與 `菌絲體` 的規則）在單次掃描下是合法的：
  長詞先匹配，目的詞受保護。判斷依據是官方用語，不是規則看起來是否乾淨；每一筆列在測試的允許清單並註明原因。
- `from`＝`to` 的保護型規則用來擋住較短規則（例如 `配置給→配置給` 擋住 `配置→設定`，`命令你→命令你` 擋住 `命令→指令`）。

## 規則也套用在繁中上

除了簡中轉繁，語系合併（`translation_tool/core/lang_merge_dict.py`）與 Mod 資料庫掃描（`translation_tool/translation_db/scanner.py`）
也會對**模組自帶的繁中**套用同一份規則，把模組作者的非官方用語換成官方用語。因此：

- 規則不能改動已經是官方繁中的文字。`tests/test_default_replace_rules.py` 的 `test_rules_keep_official_zh_tw_unchanged`
  對官方 `zh_tw.json` 每個值套用規則，結果必須完全不變；官方繁中在少數句子用到規則原詞時，用保護型規則擋住。
- 同一個詞在官方有兩種譯名、文字規則無法分辨的詞不寫規則（例如「迅捷」：效果是「加速」、藥水是「迅捷藥水」），
  不用保護規則硬湊。
- 簡中與繁中同字不同義的詞不寫規則。例如簡中物品「红砖」轉繁是「紅磚」、官方繁中是「紅磚頭」，但繁中「紅磚」本身是
  方塊（`block.minecraft.bricks`）；同理「蜂巢」（簡中蜂窩、繁中蜜脾）、「力量」（簡中附魔「強力」、繁中效果「力量」）、
  「水下呼吸」。這些官方 key 因此不一致，是刻意的取捨。
- 整段規則的原詞不能是正確的繁中寫法：`^橡木$→橡木塊` 會把模組木材選單裡的「橡木」改掉，所以不寫。

## 撰寫原則

1. 一律以官方 `assets/official_locales/minecraft/zh_cn.json` → `zh_tw.json` 為準；模組 JAR 自帶譯文只用來檢查是否傷害複合詞。
2. 譯詞不加多餘空白（官方譯名本身含空白者除外，見測試允許清單）。
3. 以「木」「磚」結尾的材料詞（`深色橡木→黑橡木`、`紅樹木→紅樹林木`、`去皮雲杉木→剝皮杉木`）
   譯詞不加「塊」「頭」：字面規則是子字串替換，會套到模組的「紅樹木椅子」「紅磚窗戶」之類的複合詞。
   官方單獨名稱需要「塊」時用整段規則（`^紅樹木$→紅樹林木塊`）。
   `tests/test_default_replace_rules.py` 的 `test_material_prefix_rules_do_not_append_a_suffix` 會擋下違反的字面規則。
4. 新增規則要是名詞性詞彙或完整官方名稱，不要放含助詞（了、的、在、是…）的句子片段。
   一般詞彙只在特定搭配下才有官方譯名時，寫成搭配（`襲擊隊長→突襲隊長`、`在襲擊中→在突襲中`），
   不要寫成會改到動詞用法的短規則（`襲擊→突襲` 會把「被閃電球襲擊了」改錯）。名詞規則必須保留時，對動詞用法加保護
   （`命令→指令` 搭配 `命令你→命令你` 等）。
6. 原詞開頭是單字材料的物品名稱（`金頭盔`、`金塊`）用整段規則：字面規則會套進「合金頭盔」「辣金頭盔」等別的材料。
   保護型規則不要寫成會出現在較長原詞中間的詞（`合金塊→合金塊` 會擋掉 `下界合金塊→獄髓方塊`），寫入前用審查工具確認
   `output_differs_from_to` 沒有新增項目。
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
結構中的重複、前後空白、交叉引用與原詞不可達（`unreachable_from`）由 `translation_tool/utils/replace_rules_diagnostics.py` 的 `diagnose_rules` 偵測，
規則頁也用同一份診斷。

模組語料的參考譯文取自 Mod 資料庫，而資料庫匯入 JAR 繁中時已經套用過當時的規則，所以參考譯文會偏向匯入當時的規則；
判斷規則是否傷害模組繁中時，請直接讀 JAR 內原始的 `zh_tw.json`。

加入新規則的驗證方式：同時在官方與模組語料回測，官方不得有任何 key 變差；模組變差的 key 要逐一確認是不是
JAR 譯文本身與官方用語不一致（以官方為準）。

## 回歸測試

`tests/test_default_replace_rules.py` 檢查：沒有前後空白與多餘空白、原詞可達、沒有重複原詞、
沒有未定案的交叉引用與輸出漂移（允許清單需寫明原因）、材料詞不加尾字、規則不改動官方繁中，
以及官方名稱類 key 的完全一致率不得低於固定門檻。改善規則後，請把門檻調高到新結果向下取整。
