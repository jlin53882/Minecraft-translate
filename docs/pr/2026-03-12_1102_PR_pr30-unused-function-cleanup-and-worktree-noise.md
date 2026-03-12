# PR30：unused functions cleanup + 工作樹雜訊收斂

## Summary
本 PR 完成低風險清理（不動 UI）：
1. 移除 5 個 repo 內無使用的舊函數
2. 補 `.gitignore` 以收斂 `.tmp/`、`.pytest-tmp/` 工作樹雜訊

---

## Phase 1 完成清單
- [x] 移除 `safe_json_loads_old`
- [x] 移除 `safe_convert_text_old`
- [x] 移除 `get_cache_size_old`
- [x] 移除 `build_minimal_dict`
- [x] 移除 `group_by_file`
- [x] `.gitignore` 新增 `.tmp/`、`.pytest-tmp/`
- [x] 清理本地 `.tmp/`、`.pytest-tmp/`（會於下次測試自動重建）

---

## 檔案變更
- `.gitignore`
- `translation_tool/core/lm_translator_main.py`
- `translation_tool/utils/text_processor.py`
- `translation_tool/utils/cache_manager.py`
- `translation_tool/core/lm_translator.py`

---

## 刪除/移除說明

### 刪除項目：5 個舊函數
- **為什麼改**：降低 dead code 噪音，避免後續重構誤判。
- **為什麼能刪**：repo 內檢索無呼叫點。
- **目前誰在用 / 沒人在用**：repo 內沒人在用。
- **替代路徑**：
  - `safe_json_loads_old` -> `safe_json_loads`
  - `safe_convert_text_old` -> `safe_convert_text`
  - 其餘 3 個為殘留 helper，無替代需求
- **風險**：repo 外部私有腳本若硬引用會失敗。
- **如何驗證**：見下方 Validation results。

### 移除項目：工作樹暫存雜訊
- **為什麼改**：避免每次測試後出現未追蹤噪音。
- **為什麼能刪**：皆為 pytest/uv 暫存產物。
- **替代路徑**：保留生成機制，交由 `.gitignore` 管理。
- **風險**：低；下次執行測試會自動生成。

---

## Validation checklist（含本次補強）
- [x] `rg -n --glob "*.py" --glob "!backups/**" "\bsafe_json_loads_old\b|\bsafe_convert_text_old\b|\bget_cache_size_old\b|\bbuild_minimal_dict\b|\bgroup_by_file\b" .`
- [x] `rg -n "build_minimal_dict\|group_by_file" . --no-ignore`
- [x] `git status --short`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr30 -o cache_dir=.pytest-cache\pr30`

---

## Validation results

```text
> rg -n --glob "*.py" --glob "!backups/**" "\bsafe_json_loads_old\b|\bsafe_convert_text_old\b|\bget_cache_size_old\b|\bbuild_minimal_dict\b|\bgroup_by_file\b" .
(no output)

> rg -n "build_minimal_dict\|group_by_file" . --no-ignore
(no output)

> uv run pytest -q --basetemp=.pytest-tmp\pr30 -o cache_dir=.pytest-cache\pr30
........................................
40 passed in 0.90s
```

補充：
- 用 `--no-ignore` 搜全 repo 時，舊符號仍會出現在 `docs/pr` 與 `backups/`（歷史紀錄與備份），這是預期行為；
  目前主程式碼路徑（排除 backups 的 `*.py`）已無引用。

---

## Rejected approaches
1) 試過：同步動 UI 大檔（例如 `cache_view.py`）一起重構。  
2) 為什麼放棄：本輪明確要求「UI 先不動」，且風險與驗證成本會顯著放大。  
3) 最終改採：先做低風險 dead code + 工作樹雜訊清理。

---

## Next step
- 若要續做 PR31，建議先鎖定「非 UI」範圍（例如 cache/search 或核心巨檔拆分設計）再進場。
