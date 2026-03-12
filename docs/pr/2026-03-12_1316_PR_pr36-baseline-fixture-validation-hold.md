# PR36 Baseline Fixture + Validation 回報（停在拆分前）

> 狀態：已完成 baseline fixture 與基準測試，**尚未進入 Phase 1 模組拆分**。

## 本次新增內容
### 新增測試檔
- `tests/test_lang_merger_zip_baseline.py`

### 測試策略
- 建立最小 zip fixture，內含：
  - `assets/demo/lang/en_us.json`
  - `assets/demo/lang/zh_cn.json`
  - `assets/demo/docs/zh_cn.extra.json`（非 Lang JSON）
- monkeypatch `lang_merger` 內的 config / replace / translate 行為，讓輸出可預測
- 鎖定以下 baseline：
  1. 輸出檔案存在性
  2. `zh_tw.json` key/value
  3. pending / filtered_pending 內容
  4. 非 Lang JSON 轉換結果
  5. generator update 狀態（開頭 log、最後 progress、無 error）
  6. quarantine 檔案不存在

---

## Validation checklist 實際輸出

### 1) baseline fixture 單測
```text
> uv run pytest -q tests/test_lang_merger_zip_baseline.py --basetemp=.pytest-tmp\pr36-baseline -o cache_dir=.pytest-cache\pr36-baseline
.                                                                        [100%]
1 passed in 0.09s
```

### 2) 全量測試
```text
> uv run pytest -q --basetemp=.pytest-tmp\pr36-baseline-full -o cache_dir=.pytest-cache\pr36-baseline-full
........................................................................ [ 85%]
............                                                             [100%]
84 passed in 1.16s
```

---

## baseline 鎖定內容（fixture 預期）
### 輸出檔案
- `assets/demo/lang/zh_tw.json`
- `待翻譯/assets/demo/lang/en_us.json`
- `待翻譯整理需翻譯/assets/demo/lang/en_us.json`
- `assets/demo/docs/zh_tw.extra.json`

### 關鍵內容
- `zh_tw.json`
  - `{ "item.demo.title": "TW:简体说明" }`
- `待翻譯/.../en_us.json`
  - `{ "item.demo.pending": "Only English" }`
- `待翻譯整理需翻譯/.../en_us.json`
  - `{ "item.demo.pending": "Only English" }`
- `assets/demo/docs/zh_tw.extra.json`
  - `{ "title": "TW:简体内容", "body": "TW:Only English" }`

### generator 狀態
- 第一筆 update 有 `分析 ZIP 檔案` log
- 所有 update `error=False`
- 最後一筆 `progress == 1.0`

---

## 數字對照
- PR36 Phase 0 baseline：`83 passed`
- 補完 baseline fixture 後：`84 passed`
- 差異：`+1`（本次新增的 zip baseline 測試）

---

## 目前停點
- ✅ baseline fixture 已建立且驗證綠燈
- ✅ 現在已具備進入 PR36 Phase 1 的前置條件
- ⛔ 尚未 commit/push，且尚未開始實際拆分 `lang_merger.py`
