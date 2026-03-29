# JAR 抽取優化實作稽核報告（Round 2）

> 日期：2026-03-20 00:48  
> 稽核對象：minecraft-translator-flet／PR #21 最新版本  
> 稽核目的：重新檢查本次 JAR extract 優化實作，確認前次 audit 提出的缺口是否已補齊  
> 稽核方式：實讀程式碼 + 對照 PR 文件與驗證結果，不以口頭描述代替驗證

---

## 一、最終結論

相較前一輪稽核，本次 PR #21 已將主要缺口補齊。  
若以《JAR 抽取（Extract）優化設計稿｜最終可執行版》的交付範圍來看，**目前可判定為通過**，已達到可交付、可 merge 水準。

### 總結判定
- ✅ **通過**：P0 config key 稽核與修正、P1 worker cap 對齊、P2 `lang_codes` config 驅動、preview/extract regex 同步、驗證與測試補齊
- ⚠️ **非阻塞提醒**：`config.example.json` 可補 `jar_extractor.lang_codes` 範例；`os.walk vs glob.glob` benchmark 若要作為長期結論，建議未來再拿大型 mods 目錄重跑一次
- ❌ **目前未見阻塞問題**

---

## 二、實際稽核檔案與材料

本次已實讀或核對以下內容：

### 程式碼檔案
- `translation_tool/checkers/variant_comparator.py`
- `translation_tool/core/jar_processor.py`
- `translation_tool/core/jar_processor_preview.py`
- `translation_tool/core/jar_processor_extract.py`（前輪已確認 P1 核心邏輯）

### PR / 文件
- `docs/pr/2026-03-20_0000_PR_jar-extract-optimization.md`

### 測試與命中清單
- `tests/test_jar_processor.py`
- `tests/test_jar_processor_discovery.py`
- `tests/test_jar_processor_preview.py`
- `tests/test_worker_cap.py`
- 相關 `rg` 命中結果

---

## 三、分項稽核結果

## 3.1 P0｜全專案 `translation` → `translator` config key 稽核

### 前次狀態
前一輪只能確認：
- `jar_processor_extract.py` 單點已修
- 但未看到完整全專案交付，因此不能判定 P0 完成

### 本次檢查結果
本次看到：

#### 1. `variant_comparator.py` 已修正
```python
rules = load_replace_rules(config.get("translator", {}).get("replace_rules_path", "replace_rules.json"))
```

#### 2. PR 文件已補全專案稽核交付
PR 文件中已明確說明：
- 搜尋方式
- 命中清單
- 唯一需修改點
- 其餘 `.py` 不需修改
- 搜尋後已無 `get("translation")` 殘留

### 判定
✅ **P0 通過**

### 備註
這裡合理的表述是：
> 真正屬於 config key 存取的 `translation` 命中已清乾淨。

不需要誇張成「整個 repo 完全沒有任何 translation 字串」。

---

## 3.2 P1｜`max_workers` 與 merge pipeline 對齊

### 前次狀態
前次已確認 `jar_processor_extract.py` 核心邏輯正確，但缺驗證證據。

### 本次檢查結果
`jar_processor_extract.py` 仍維持：

```python
cpu_count = os.cpu_count() or 2
max_allowed_workers = max(1, cpu_count // 2)
config_workers = load_config().get("translator", {}).get("parallel_execution_workers")
if isinstance(config_workers, int) and config_workers > 0:
    max_workers = min(config_workers, max_allowed_workers)
else:
    max_workers = max_allowed_workers
```

PR 文件也補了 6 種情境驗證：
- config=4 → PASS
- config=32 → PASS（capped）
- config=None → PASS
- config=0/-1/'abc'/[]/{} → PASS
- CPU=8, config=32 → PASS
- CPU=4, config=32 → PASS

### 判定
✅ **P1 通過**

---

## 3.3 P2｜`lang_codes` config 驅動

### 前次狀態
前一輪尚未完成，仍是硬編碼 `en_us|zh_cn|zh_tw`。

### 本次檢查結果
在 `jar_processor.py` 已看到新增：

```python
def get_lang_codes() -> list[str]:
```

以及：

```python
def build_lang_file_regex() -> re.Pattern:
```

而 `extract_lang_files_generator()` 已改成：

```python
lang_file_regex = build_lang_file_regex()
```

### 判定
✅ **P2 通過**

### 補充說明
這代表：
- 不再寫死語言清單
- 可由 config 控制
- 未填 config 時仍 fallback 舊行為

---

## 3.4 P2｜preview / extract 是否同步使用同一份 lang regex

### 本次檢查結果
在 `jar_processor_preview.py` 已看到：

```python
from translation_tool.core.jar_processor import build_lang_file_regex
target_regex = build_lang_file_regex()
```

### 判定
✅ **通過**

### 意義
這代表 preview 與 extract 已同步，避免：
- extract 支援新語言
- preview 卻仍看不到

這是這次修改的重要完整性指標。

---

## 3.5 P2｜Preview I/O 前提是否仍正確

### 本次檢查結果
`jar_processor_preview.py` 仍維持：
- 使用 `zf.infolist()`
- 使用 `member.file_size`
- 未看到 `zf.open(...).read()` 內容讀取

### 判定
✅ **通過**

### 備註
這表示設計稿中的前提仍正確：
> preview 不是重複 I/O 的優化目標。

---

## 3.6 P2｜`find_jar_files()` benchmark 與維持 `os.walk`

### 本次檢查結果
PR 文件已補 benchmark：
- `os.walk`：442ms
- `glob.glob`：872ms
- 結論一致：`os.walk` 較快

### 判定
✅ **通過**

### 備註
目前證據強度足以支持：
> 在本機測試環境下，維持 `os.walk` 是合理決策。

但若未來要把它提升成「長期性能結論」，建議再拿大型 mods 目錄做一次測量。

---

## 3.7 py_compile / pytest / 功能驗證

### 本次檢查結果
PR 文件已補：

#### py_compile
- `translation_tool/core/jar_processor.py`
- `translation_tool/core/jar_processor_preview.py`
- `translation_tool/core/jar_processor_extract.py`
- `translation_tool/checkers/variant_comparator.py`

#### pytest
- `34/34 PASSED`

#### lang_codes 功能驗證
- 預設 fallback PASS
- 預設 regex 行為一致 PASS
- `ja_jp / ko_kr` config → regex 行為一致 PASS
- 空 lang_codes fallback PASS

### 判定
✅ **通過**

---

## 四、總體判定

## ✅ 已完成
1. `translation` → `translator` config key 稽核與修正已完成
2. `max_workers` 已與 merge pipeline 對齊，採 `CPU // 2` capped 邏輯
3. `lang_codes` config 驅動已落地
4. preview / extract 已同步使用動態 lang regex
5. preview 非重複 I/O 的前提持續正確
6. `find_jar_files()` benchmark 已補，現階段維持 `os.walk` 合理
7. `py_compile` / `pytest` / 功能驗證已補齊
8. PR 文件已補修改清單與驗證結果，交付完整度達標

## ⚠️ 非阻塞提醒
1. `config.example.json` 建議補 `jar_extractor.lang_codes` 範例
2. 若未來要把 benchmark 作為長期性能結論，建議在大型 mods 目錄再重跑一次

## ❌ 目前未見阻塞項
- 以本次設計稿範圍來看，**目前沒有新的阻塞問題**

---

## 五、可直接對外使用的結論

> PR #21 已把前次 audit 提出的主要缺口補齊；就 JAR extract 優化設計稿範圍而言，現在已達到 **可交付、可 merge** 水準。

---

## 六、最終建議

目前這顆 PR 若沒有其他外部 review comment，已可進入：
- merge
- 或交由下一位 reviewer 做最終 approve

本輪不建議再為了追求形式完美而繼續延長修改週期，因為核心要求已滿足，繼續拖下去的收益有限。
