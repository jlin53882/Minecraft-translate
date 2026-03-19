# Merge v3 第二輪程式碼稽核報告｜收尾版

> 日期：2026-03-19 20:08
> 稽核對象：本地已再次修改的 merge pipeline / view / config 程式碼
> 對照基準：`docs/pr/2026-03-19_1612_PR_merge_pipeline_optimization_design_v3.md`
> 稽核結論：**大部分已補齊，剩 1 個必修邏輯點 + 2 個待補證明項**

---

## 一、總結

這次比上一輪明顯進步，已從「退回補修」提升到：

### 目前判定
- **完成度：約 85~90%**
- **狀態：進入最後一輪補修 / 補驗證**

---

## 二、已確認修正完成

### 1. `max_workers` capped version 已補齊
已確認：
- `lang_merger.py`
- `jar_processor_extract.py`

都已改成與 merge / extract 統一的保守策略：
- `CPU // 2`
- config 值也會被 cap

**判定：✅ 完成**

---

### 2. façade 透傳參數已補上
已確認 `lang_merge_content.py` 已新增：
- `patchouli_eff_cache`

且已往下透傳到 `process_content_or_copy_file_impl()`。

**判定：✅ 完成**

---

### 3. Patchouli effectiveness 快取已加入
已確認 `lang_merge_content_copy.py` 已有：
- module-level `_patchouli_eff_cache`
- cache lookup / cache store
- 外部 `patchouli_eff_cache` 入口

**判定：✅ 基本完成**

---

### 4. `contains_cjk()` 已改為 `lru_cache` 路線
已確認 `lang_merge_pipeline.py` 已加入：
- `from functools import lru_cache`
- `_contains_cjk_str()`

**判定：✅ 部分完成，但仍有一個邏輯點要修（見下節）**

---

### 5. runtime guard 已補進 Patchouli 判斷
已確認：
```python
allow_zh_cn = False if not process_zh_cn else bool(...)
```

這點已符合 v3 要求的 runtime guard 精神。

**判定：✅ 完成**

---

### 6. UI 互鎖與 disabled note 已存在
已確認 `merge_view.py` 有：
- `process_zh_cn_switch`
- `skip_zh_cn_switch`
- `patchouli_skip_zh_cn_switch`
- `patchouli_threshold_field`
- disable / note 顯示

**判定：✅ UI 雛形完成**

---

## 三、剩餘必修項（1 項）

# 必修 1｜`_contains_cjk(v)` 的 list/dict 遞迴邏輯被改壞了

## 現況
目前 `lang_merge_pipeline.py`：

```python
def _contains_cjk(v) -> bool:
    if isinstance(v, str):
        return _contains_cjk_str(v)
    return bool(CJK_RE.search(str(v)))
```

## 問題
這和原本設計 / 原始語義不同。

原本 `contains_cjk(v)` 應該：
- `str` → 判斷字串
- `list` → 遞迴檢查每個元素
- `dict` → 遞迴檢查每個 value

現在這版把 `list/dict` 直接 `str(v)` 後再 regex 檢查，存在以下風險：
- 可能受到 Python representation 影響
- 結構型資料的語義判斷與原本不一致
- 屬於邏輯層面的退化，不只是寫法差異

## 必修要求
請改成：

```python
def _contains_cjk(v: Any) -> bool:
    if isinstance(v, str):
        return _contains_cjk_str(v)
    if isinstance(v, list):
        return any(_contains_cjk(x) for x in v)
    if isinstance(v, dict):
        return any(_contains_cjk(x) for x in v.values())
    return False
```

**判定：❌ 必修，修完才能算邏輯驗收通過**

---

## 四、待補證明項（2 項）

# 待補證明 1｜`lang_merger.py` 是否真的需要預掃描並下傳外部 cache

## 現況
雖然 façade 與 content_copy 已支援：
- `patchouli_eff_cache`

但目前 `lang_merger.py` 尚未看到：
- 先預掃描建立 cache
- 再把 cache 傳進 `_process_content_or_copy_file(...)`

## 說明
目前因為已有 module-level cache，功能上不一定壞；但若要**完全對齊 v3 設計稿**，還需要補這一步。

## 判定
**⚠️ 非必修，但若要說「完全照設計稿完成」就要補上或說明為何保留 module cache 方案**

---

# 待補證明 2｜儲存 config 時的 normalization 實作位置

## 現況
目前 `merge_view.py` 有做到：
- UI disable
- note 顯示
- 關閉主開關時把依賴開關 value 設為 False

但這仍不等於：
- config 寫回前一定做 normalization

## 需要提供的證明
請指出實際寫回 config 的程式位置，並證明：

當：
```python
process_zh_cn_files == False
```

儲存前會強制：
```python
skip_zh_cn_when_only_process_lang = False
patchouli_skip_en_us_when_zh_cn_exists = False
```

## 判定
**⚠️ 待補證明；若沒有，就要補做**

---

## 五、驗收結論

### 可確認已達成
- 大部分 v3 需求已落地
- 核心 worker 策略、Patchouli 快取、runtime guard、UI 互鎖已有成形

### 目前不能直接宣告 100% 完成的原因
還差：
1. **1 個必修邏輯修正**：`_contains_cjk(v)` 遞迴語義要補回
2. **2 個待補證明**：
   - 是否有 `lang_merger.py` 外部預掃描 cache
   - 是否有 config save normalization

---

## 六、最終一句話（給修正者）

目前已接近可驗收，請再補完以下內容：

1. 修正 `lang_merge_pipeline.py` 的 `_contains_cjk(v)`，恢復 list/dict 遞迴判斷
2. 補充說明或實作 `lang_merger.py` 的外部預掃描 cache 下傳
3. 提供 config 寫回時 normalization 的實作位置；若尚未做，請補上
