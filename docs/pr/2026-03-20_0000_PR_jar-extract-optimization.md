# PR #21｜JAR 抽取優化：Config Key 稽核 + lang_codes 動態化

> 日期：2026-03-20 00:00
> 狀態：**已實作 / 已驗證**
> 適用版本：minecraft-translator-flet 0.6.0+

---

## 1. 問題背景

### 1.1 P0｜錯誤 config key（`translation` 應為 `translator`）

全專案稽核結果（使用 python script 全目錄搜尋 `get("translation")` / `["translation"]`）：

| 檔案 | 行號 | 類別 | 說明 |
|------|------|------|------|
| `variant_comparator.py` | 30 | **需修改（已修）** | `config.get("translation")` → `config.get("translator")` |
| 其餘所有 `.py` | — | 不用修改 | 皆已正確使用 `translator` key |

**搜尋結果**：在 `fix/jar-extract-optimization` branch 上已無 `get("translation")` 殘留。
**統計**：全專案共 20 處正確使用 `translator` key。

### 1.2 P2｜LANG_CODES 寫死，無法擴充

原本 `jar_processor.py` 內 `LANG_CODES` 是靜態 list，導致無法支援 `ja_jp`、`ko_kr` 等額外語言，且 `jar_processor_preview.py` 也有相同寫死問題。

---

## 2. 實作內容

### 2.1 P0｜修正 config key（1 個檔案）

- `translation_tool/checkers/variant_comparator.py:30`
  - 修正前：`config.get("translation", {}).get("replace_rules_path", ...)`
  - 修正後：`config.get("translator", {}).get("replace_rules_path", ...)`

### 2.2 P2｜lang_codes 改為 config 驅動

**新增 `get_lang_codes()` / `build_lang_file_regex()` 輔助函式**（`jar_processor.py`）：

```python
def get_lang_codes() -> list[str]:
    cfg = load_config()
    codes = cfg.get("jar_extractor", {}).get("lang_codes", ["en_us", "zh_tw", "zh_cn"])
    if not isinstance(codes, list) or not codes:
        codes = ["en_us", "zh_tw", "zh_cn"]
    return codes

def build_lang_file_regex() -> re.Pattern:
    codes = get_lang_codes()
    codes_str = "|".join(map(re.escape, codes))
    return re.compile(rf"(?:assets/([^/]+)/)?lang/({codes_str})\.(json|lang)$", re.IGNORECASE)
```

**同步更新 `jar_processor_preview.py`**：
- lang mode preview 現在也使用 `build_lang_file_regex()`，與 extract 流程完全一致

**config.json 建議格式**（未填寫時自動退階為舊行為）：
```json
{
  "jar_extractor": {
    "lang_codes": ["en_us", "zh_cn", "zh_tw", "ja_jp", "ko_kr"]
  }
}
```

### 2.3 P1｜max_workers 邏輯（已驗證已合規，無需修改）

確認 `jar_processor_extract.py` 的 worker 邏輯已與 `lang_merger.py` 完全一致：
- `cpu_count // 2` 上限
- config 可 override，但不得超過 `cpu_count // 2`

兩者實作完全相同，無需變更。

### 2.4 P2｜`find_jar_files()` benchmark（維持 os.walk）

**Benchmark 環境**：`.pytest-tmp`（80 個 JAR，5 次測量）

| 方法 | 平均耗時 | 備註 |
|------|---------|------|
| `os.walk` | 442ms | ✅ 本環境更快 |
| `glob.glob` | 872ms | +97.2% 較慢 |
| 結果一致性 | ✅ 相同 | |

**結論**：在本機環境 `os.walk` 穩定更快，維持現況。

### 2.5 P2｜Preview I/O（確認非優化項目）

確認 `jar_processor_preview.py` 只使用 `zf.infolist()` + `member.file_size`，未做內容讀取，不存在「preview 重複 I/O」的問題。維持現況。

---

## 3. 修改檔案清單

| 檔案 | 修改類型 | 說明 |
|------|---------|------|
| `translation_tool/checkers/variant_comparator.py` | 修改 | P0：config key `translation` → `translator` |
| `translation_tool/core/jar_processor.py` | 修改 | P2：新增 `get_lang_codes()` / `build_lang_file_regex()`；extract 改用動態 regex |
| `translation_tool/core/jar_processor_preview.py` | 修改 | P2：lang mode 改用 `build_lang_file_regex()` |
| `tests/test_jar_processor.py` | 修改 | API 适配：`LANG_CODES` → `get_lang_codes()` + `build_lang_file_regex()` |

---

## 4. 向後相容說明

- config 未填 `jar_extractor.lang_codes` 時，自動退階為 `["en_us", "zh_tw", "zh_cn"]`，與修改前行為完全一致
- `variant_comparator.py` 的 config key 修正是 bug fix，不影響其他設定路徑
- `__all__` 已更新，移除已淘汰的 `LANG_CODES` 與 `lang_pattern` export

---

## 5. 驗證結果（完整交付）

### 5.1 py_compile

```
✅ translation_tool/core/jar_processor.py
✅ translation_tool/core/jar_processor_preview.py
✅ translation_tool/core/jar_processor_extract.py
✅ translation_tool/checkers/variant_comparator.py
```

### 5.2 import 測試

```python
>>> from translation_tool.core.jar_processor import get_lang_codes, build_lang_file_regex
>>> get_lang_codes()
['en_us', 'zh_tw', 'zh_cn']
>>> build_lang_file_regex().pattern
'(?:assets/([^/]+)/)?lang/(en_us|zh_tw|zh_cn)\\.(json|lang)$'
```

### 5.3 pytest

```
tests/test_jar_processor.py            14/14 PASSED
tests/test_jar_processor_discovery.py    7/7  PASSED
tests/test_jar_processor_preview.py      13/13 PASSED
==============================================
34 passed in 0.26s
```

### 5.4 max_workers 四種情境驗證

```
=== max_workers 驗證（CPU=16為例）===
情境1 [PASS] config=4 → worker=4
情境2 [PASS] config=32 → worker=8 (capped)
情境3 [PASS] config=None → worker=8
情境4 [PASS] config=0/-1/'abc'/[]/{{}} → worker=8
情境5 [PASS] CPU=8,  config=32 → worker=4
情境6 [PASS] CPU=4,  config=32 → worker=2
全部 6 情境 PASS
```

### 5.5 lang_codes config 驅動功能驗證

```
=== lang_codes config 驅動驗證 ===
[PASS] 預設 lang_codes = ['en_us', 'zh_tw', 'zh_cn']
[PASS] 預設 regex 匹配行為正確
[PASS] ja_jp / ko_kr config 時 regex 行為正確
[PASS] 空 lang_codes fallback 正確
全部 lang_codes 驗證 PASS
```

---

## 6. 待確認事項（後續）

- [ ] 若日後支援 `ja_jp` / `ko_kr`，需在 `config.example.json` 同步更新範例
- [ ] `os.walk` vs `glob.glob` benchmark 建構在 80 檔樣本上（`.pytest-tmp`），建議在真實 mods 目錄（1000+ JAR）再次驗證

---

⚠️ 請檢查以上結果，確認沒問題後再 merge PR。
