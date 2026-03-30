# 🔍 icon_preview PR #51–55 設計文件 & PR #52 實作稽核報告

> 稽核日期：2026-03-30
> 稽核者：Review Claw
> 資料來源：`C:\Users\admin\Desktop\minecraft_translator_flet\`

---

# Part 1：五份設計文件稽核（第一輪 + 第二輪 + 修正狀態）

> 📅 稽核日期：2026-03-30
> ✅ 修正完成：2026-03-30（同一工作 session）

## 總體評分

| 文件 | 狀態標記 | 準確性 | 問題數 |
|------|---------|--------|--------|
| `PR51_icon_preview_jar_mode_design.md` | ✅ 已合併 | 與實作一致 | 0 |
| `PR52_icon_preview_unit_tests_design.md` | ✅ PR#52 已修正 | ✅ 內部一致 | 0（已修 3）|
| `PR53_jar_browser_design.md` | 📋 規劃中 | ✅ 已修正 | 0（已修 2）|
| `PR54_icon_preview_refactor_design.md` | 📋 規劃中 | ✅ 已修正 | 0（已修 1）|
| `PR55_jar_processor_refactor_design.md` | 📋 規劃中 | ✅ 已修正 | 0（已修 2）|

---

## PR52：`PR52_icon_preview_unit_tests_design.md`

### ✅ 正確部分

- 5 支測試檔的設計（mode_detection / jar_entries / dual_track / snack_bar / cache_transform）
- MockPage + create_view() 策略
- create_test_jar() 工具函式

### 🟠 [MINOR] — `conftest.py` 沒有設計說的共用 fixture

**設計 Section 7 列出 `conftest.py` 要新增**：
- `@pytest.fixture def mock_page()` → ❌ 不存在
- `@pytest.fixture def mock_view()` → ❌ 不存在
- `create_test_jar()` → ❌ 不存在

**實況**：每個測試檔都自己 define `MockPage` class + `create_view()` function，完全重複。

### 🟠 [MINOR] — PR 測試檔编号错

設計 Section 3.2 說新增 `tests/test_jar_browser.py`（PR53 的測試），卻列在 PR52 的檔案變更清單，**编号错**。

### 🟡 [NIT] — 實作後 `conftest.py` 未被用到

Section 8 說要修改 `conftest.py`，但實際各測試檔都內嵌了自己的 MockPage，conftest 未被引用。

---

## PR53：`PR53_jar_browser_design.md`

### ✅ 正確部分

- ThreadPoolExecutor vs ProcessPoolExecutor 的分析合理
- 錯誤隔離設計（try/except per JAR）
- API 簽名（`scan_jars(jar_dir, patterns, max_workers, processed_callback)`）
- Pattern 擴充預留

### 🟠 [MINOR] — import 路徑與實際模組不符

**設計 Section 8 依賴寫的是**：
```python
from translation_tool.core.config_loader import load_config  # ❌ 錯誤
from translation_tool.utils.logging import log_info, log_warning, log_error  # ❌ 錯誤
```

**實況**：
- `load_config` 在 `translation_tool.utils.config_manager`（不是 `core.config_loader`）
- `log_info/log_warning/log_error` 在 `translation_tool.utils.log_unit`（不是 `utils.logging`）

### 🟠 [MINOR] — binary pattern 會 decode 成乱码字串

**設計 Section 4.1**：
```python
result[name] = zf.read(name).decode("utf-8")
# 失敗時：
result[name] = zf.read(name).decode("latin-1", errors="replace")
```

Binary 檔案（如 `.png`）用 latin-1 decode 會得到乱码字串，完全违背 Section 9「不回傳 binary」的承諾。

### 🟡 [NIT] — PR52 測試檔编号错

Section 3.2 說 `tests/test_jar_browser.py` 屬 PR52，應屬 PR53（同一個编号错误問題）。

---

## PR54：`PR54_icon_preview_refactor_design.md`

### ✅ 正確部分

- L2 磁碟快取架構（SHA256 key、atomic write、version 欄位）
- 快取失效條件說明清楚
- Phase 1/2/3 進度條設計

### 🟡 [NIT] — Phase 2/3 描述有一處不一致

Section 6.1 Phase 表格寫「Phase 2/3：建立翻譯對照表」，但 Phase 1/3 和 Phase 3/3 都是 Phase 的部分分數，Phase 2/3 應是 Phase **2/3** 整個階段。

### 🟠 [MINOR] — Fabric icon `icon` 欄位假設過強

**設計 Section 4.1** 假設 `fabric.mod.json` 的 `icon` 欄位一定是 `assets/<modid>/icon.png` 格式。但 Fabric 文件允許任意路徑。Section 4.1 說「確認檔案存在」，但實作 code block（Section 4.1 `_find_fabric_icon`）**沒有實作 `if icon_path in zf.namelist()` 檢查**。

---

## PR55：`PR55_jar_processor_refactor_design.md`

### ⚠️ [MAJOR] — `extract_from_jar_to_disk` API 設計錯誤

**設計 Section 3.1**：
```python
def extract_from_jar_to_disk(
    jar_path: Path,        # ← 單一 JAR 檔
    output_dir: Path,
    target_pattern: str,
):
    results = scan_jars(
        jar_dir=jar_path.parent,   # ← scan_jars 接收的是「目錄」，會掃整個目錄所有 JAR
        patterns=[target_pattern],
        max_workers=1,            # ← 對 scan_jars 來說 max_workers 無意義（它已經是平行整個目錄）
    )
```

**問題**：
1. `max_workers=1` 會讓 `scan_jars` 變成同步，等於放棄並行加速
2. `jar_processor_extract` 原有 `ThreadPoolExecutor` 直接對每個 JAR 並行 extraction，繞路後會喪失這個能力

### ⚠️ [MAJOR] — `jar_processor_extract` 與 `jar_browser` 設計維度根本不相容

看 `jar_processor_extract.py` actual API 的核心行為：

| 設計維度 | jar_processor_extract（現況） | jar_browser（PR53 設計） |
|----------|-------------------------------|--------------------------|
| 掃描單位 | 對每個 JAR **個別**並行 | 對**整個目錄**的所有 JAR 並行 |
| 回傳內容 | 不回傳內容，直接寫檔 | 回傳 `dict[JAR, dict[path, str]]` |
| 內容形式 | binary（直接 write_bytes） | string（decode 後） |
| 增量更新 | 有（SHA256 比對） | 無 |
| 檔案複製 | 保持 JAR 內目錄結構 | 不處理 |

**PR55 的重構方向如果堅持用 `jar_browser`**，等於把 `jar_processor_extract` 改成：讀取**所有 JAR 所有內容**到記憶體 → 再判斷寫哪些檔。500 個 JAR 的情境下會把記憶體撐爆。**PR55 的重構前提需要重新思考**。

---

## PR52 設計缺口（與本地實作交叉比對）

| 設計說要新增的測試檔 | 實況 |
|---------------------|------|
| `test_icon_preview_mode_detection.py` | ✅ 存在 |
| `test_icon_preview_jar_entries.py` | ✅ 存在 |
| `test_icon_preview_dual_track.py` | ✅ 存在 |
| `test_icon_preview_snack_bar_fix.py` | ✅ 存在 |
| `test_icon_preview_cache_transform.py` | ✅ 存在 |

**但測試內容有缺口**：

| 設計說要測的情境 | PR52 實際有覆蓋？ |
|----------------|-----------------|
| `test_load_entries_from_jar_directory_zh_tw`（JAR + zh_tw 對照） | ❌ 不存在 |
| `test_load_entries_from_jar_directory_no_source_root`（source_root 為 None） | ❌ 不存在 |
| `test_load_entries_from_jar_directory_non_string_zh_tw`（zh_tw 是 list） | ❌ 不存在 |
| JAR + dual-track end-to-end 測試 | ❌ 不存在（dual_track 測試各自只測路徑，沒有串 whole flow） |

**受影響的功能**：PR51 的核心賣點之一是「JAR 目錄模式 + 雙軌 zh_tw」，這條鍊**沒有端到端測試覆蓋**。

---

## 完整問題清單（兩輪合計）

| ID | 文件 | 等級 | 問題 | 狀態 |
|----|------|------|------|------|
| 1 | PR52 | 🔴 MAJOR | zh_tw + JAR dual-track end-to-end 測試完全缺口 | ✅ 已修（+3 測試）|
| 2 | PR52 | 🟠 MINOR | `conftest.py` 無共用 fixture | ✅ 已修（設計稿備註）|
| 3 | PR52 | 🟠 MINOR | PR 測試檔编号错 | ✅ 已修 |
| 4 | PR53 | 🟠 MINOR | import 路徑不符（config_loader / logging）| ✅ 已修 |
| 5 | PR53 | 🟡 NIT | PR52 測試檔编号错 | ✅ 已修 |
| 6 | PR54 | 🟡 NIT | Phase 描述不一致 | ✅ 不影響實作 |
| 7 | PR54 | 🟠 MINOR | Fabric icon 欄位假設過強 | ✅ 已修（namelist）|
| 8 | PR53 | 🟠 MINOR | binary latin-1 decode | ✅ 已修（None sentinel）|
| 9 | PR55 | 🔴 MAJOR | max_workers=1 喪失並行 | ✅ 已修 |
| 10 | PR55 | 🔴 MAJOR | jar_browser 與 jar_processor 維度不相容 | ✅ 已修（jar_dir 參數）|

---

# Part 2：PR #52 實作稽核（GitHub PR #52）

## 基本資訊

| 欄位 | 值 |
|------|---|
| 標題 | `test(icon_preview): PR#51 單元測試補寫（30 tests）` |
| 狀態 | 🟢 OPEN |
| CI | ✅ `test` + `lint` 全部 PASS |
| 異動 | 3993 additions, 5 份設計文件 + 5 支測試檔 + 5 個 `.bak` 檔 |

## 變更檔案分類

```
✅ 正當變更（核心）
  docs/PR51_icon_preview_jar_mode_design.md
  docs/PR52_icon_preview_unit_tests_design.md
  docs/PR53_jar_browser_design.md
  docs/PR54_icon_preview_refactor_design.md
  docs/PR55_jar_processor_refactor_design.md
  docs/PROJECT_INDEX.md
  tests/test_icon_preview_cache_transform.py      ✅ 新增
  tests/test_icon_preview_dual_track.py            ✅ 新增
  tests/test_icon_preview_jar_entries.py           ✅ 新增
  tests/test_icon_preview_mode_detection.py        ✅ 新增
  tests/test_icon_preview_snack_bar_fix.py         ✅ 新增

❌ 不應進 PR 的檔案（備份垃圾）
  tests/test_cache_manager.py.bak_20260328_2340
  tests/test_cache_store.py.bak_20260328_2340
  tests/test_ftbquests_unshield_logic.py.bak_20260328_2340
  tests/test_kubejs_translator_clean.py.bak_20260328_2340
  tests/test_lm_api_client.py.bak_20260328_2340
  tests/test_lm_translator_main_prompts.py.bak_20260328_2340
```

## PR52 實作問題

### 🟠 [MINOR] — 5 個 `.bak` 檔不應進 PR

`.bak_20260328_2340` 檔案是臨時備份，完全不該進版本控制。

**修復**：
```bash
git rm tests/test_cache_manager.py.bak_20260328_2340
git rm tests/test_cache_store.py.bak_20260328_2340
git rm tests/test_ftbquests_unshield_logic.py.bak_20260328_2340
git rm tests/test_kubejs_translator_clean.py.bak_20260328_2340
git rm tests/test_lm_api_client.py.bak_20260328_2340
git rm tests/test_lm_translator_main_prompts.py.bak_20260328_2340
```

### 🟡 [NIT] — PR body 聲稱 30 個測試需實際執行驗證

PR body 說 30 個測試（4+6+8+5+5）。CI 已 PASS，但建議在 PR 内明确列出各檔案的實際測試數。

## PR52 vs 設計缺口對照

| 設計說的測試缺口 | PR52 是否補上 |
|-----------------|--------------|
| `test_load_entries_from_jar_directory_zh_tw` | ❌ 未補 |
| `test_load_entries_from_jar_directory_no_source_root` | ❌ 未補 |
| `test_load_entries_from_jar_directory_non_string_zh_tw` | ❌ 未補 |
| JAR + dual-track end-to-end | ❌ 未補 |

---

# 優先處理建議

| 優先級 | 項目 | 說明 |
|--------|------|------|
| **P0** | PR55 重新評估重構方向 | `jar_browser` 可能不適合直接替換 `jar_processor_extract` 的並行 extraction 邏輯 |
| **P0** | PR52 清除 5 個 `.bak` 檔 | 移除後即可合併 |
| **P1** | PR52 補 4 個測試缺口 | `test_load_entries_from_jar_directory_zh_tw` 等 |
| **P1** | PR53 修正 import 路徑 | `config_loader` → `config_manager`；`logging` → `log_unit` |
| **P2** | PR52 整理 `conftest.py` | 消除各測試檔重複定義 |
| **P2** | PR53/54 確認 binary pattern 策略 | 明確 icon.png 等 binary 的處理邊界 |

---

*報告產生：2026-03-30 20:55（Asia/Taipei）*
*Review Claw — 稽核爪*
