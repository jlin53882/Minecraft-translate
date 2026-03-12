# DOCSTRING_SPEC.md

本檔定義本 repo 的 **docstring 撰寫規格**。

目標：
- docstring 要「有資訊量」且「不誤導」
- 避免出現模板廢話（例如：用途說明 / 參數請見函式簽名 / 回傳內容依實作而定）
- 後續每次開 PR 前可用本規格對照，確保風格一致

> 位置：長期參考文件 → 放 `docs/`（不放 `docs/pr/`）。

---

## 1) 核心原則（Hard rules）

1. **只寫能被程式碼直接驗證的事實**
   - 寧可短、也不要寫錯
   - 不推測 domain 行為、不宣稱不存在的契約

2. **避免模板廢話**
   - 禁止：
     - 「用途說明」
     - 「參數請見函式簽名」當作唯一內容
     - 「回傳內容依實作而定」當作唯一內容
   - 允許「參數：依函式簽名」這句，但前提是 docstring 其他部分有足夠資訊量。

3. **避免噪音 callee**
   - 不要把 `float` / `max` / `min` 這類 builtins 當作「主要呼叫」寫進 docstring。

---

## 2) UI sizing helper（你指定的模板）

適用：
- 任何依 `page.width/page.height` 計算 panel 寬高
- 任何含 fallback / clamp / ratio 的 UI sizing helper

### 格式（固定）

```text
"""一句話說用途（影響哪個 UI 區塊）。

規則：
- fallback 條件 → 回傳值
- 正常：公式（比例），clamp 上下限
"""
```

### 範例（`_dynamic_shard_key_panel_width`）

```python
def _dynamic_shard_key_panel_width(self) -> int:
    """計算 Shard Key panel 的寬度（跟視窗寬度自適應）。

    規則：
    - 若 page.width 取得失敗或 <= 0 → 360
    - 正常：int(page.width * 0.30)，clamp 280..560
    """
```

---

## 3) Generator / service wrapper（run_*_service 類）

適用：
- `run_*_service()`
- 任何會 `yield update_dict` 給 UI 的 generator

### 必寫要點
- 這是 generator（會逐步 yield update dict）
- update dict 典型 key（例如：`log` / `progress` / `error`）
- 主要包裝/呼叫的「專案內」generator（例如 `bundle_outputs_generator`）

### 範例

```python
def run_bundling_service(input_root_dir: str, output_zip_path: str):
    """UI pipeline wrapper：執行輸出打包並逐步回報進度。

    - 主要包裝：bundle_outputs_generator(...)
    - 以 generator 形式 yield update dict（常見包含 log/progress/error）
    """
    ...
```

---

## 4) 其他一般函式（非 UI sizing、非 generator）

建議格式（簡短版）：
- 1 句用途
- 1~3 個 bullets 描述「輸入/輸出/副作用」中最重要的那一項

範例：

```python
def load_config() -> dict:
    """從 project root 載入 config.json（找不到時使用預設設定）。"""
```

---

## 5) PR review checklist（針對 docstring 的檢查點）
- [ ] docstring 是否提供了「維護者需要的資訊」而非模板廢話
- [ ] 是否避免寫錯契約（只寫 code 可驗證的事實）
- [ ] UI sizing helper 是否包含：fallback / ratio / clamp
- [ ] generator/service wrapper 是否清楚標示：yield update dict + 主要包裝哪個 core generator
