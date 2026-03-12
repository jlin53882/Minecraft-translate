# PR56 設計稿：`rules_view.py` + `config_view.py` 表單 / CRUD 結構整理

## Summary
PR56 收的是兩顆典型表單型 view。這兩支不需要過度架構化，但至少要把 state、row builder、load/save action 分層，不然每次調整規則或設定欄位都像在碰一個大黏土球。

---

## Phase 0 盤點
- 目前 `app/views/rules_view.py` 約 545 行，`app/views/config_view.py` 約 443 行。
- 兩支 view 都包含大量表單 state、row builder、load/save/reload action，而且 validation / snackbar / dirty state 也混在主類別內。
- 這兩支是最典型的『功能不算難，但長期維護很累』型 UI。
- PR51 已先要求補 characterization tests，PR56 才能安全調整內部結構。

---

## 設計範圍
- 對 `rules_view.py`：新增 `app/views/rules/rules_state.py`、`rules_actions.py`、`rules_table.py`，把 pagination/search/sort、row validation、save/reload 從主類別拔出。
- 對 `config_view.py`：新增 `app/views/config/config_state.py`、`config_actions.py`、`config_form.py`，把模型清單、key rows、load/save mapping 與 UI 組裝分開。
- 兩支 view 都維持現在的使用流程與欄位長相，不改產品行為。
- focused tests要鎖：load/save、dirty state、validation/dialog/snack path。

---

## Validation checklist
- [ ] `rg -n "def load_config|def save_config_clicked|def _load_rules_core|def save_rules_clicked|dirty|validate_rule" app/views/config_view.py app/views/rules_view.py app/views/config app/views/rules --glob "*.py"`
- [ ] `uv run pytest -q tests/test_config_view_characterization.py tests/test_rules_view_characterization.py --basetemp=.pytest-tmp\pr56 -o cache_dir=.pytest-cache\pr56`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr56-full -o cache_dir=.pytest-cache\pr56-full`

---

## Rejected approaches
1) 試過：把 config / rules 共用的表單能力抽成超通用 form framework。
2) 為什麼放棄：這聽起來很潮，但 scope 會瞬間膨脹成 UI 基礎框架設計，根本不是單顆 refactor PR 應該做的事。
3) 最終改採：只針對這兩支 view 做在地拆分，保持低風險與高可讀性。

---

## Not included in this PR
- 不重畫表單。
- 不調整設定 schema。
- 不順手合併 rules/config view。

---

## Next step
- PR57 才做 dead code / leftover compatibility cleanup。
