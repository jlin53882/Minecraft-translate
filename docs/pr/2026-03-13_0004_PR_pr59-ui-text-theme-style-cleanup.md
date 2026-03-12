# PR59：UI text theme_style cleanup

## Summary
這顆 PR 是獨立的 UI deprecation cleanup，只處理一件事：把 Flet 過時的 `ft.Text(style=ft.TextThemeStyle.xxx)` 改成 `ft.Text(theme_style=ft.TextThemeStyle.xxx)`，不混任何其他 refactor 或功能改動。

---

## Phase 1 完成清單
- [x] 做了：鎖定 61 warnings 的實際來源檔案。
- [x] 做了：只修改以下檔案中的 `TextThemeStyle` 用法：
  - `app/views/bundler_view.py`
  - `app/views/config/config_form.py`
  - `app/views/lookup_view.py`
  - `app/views/qc_view.py`
  - `app/views/rules_view.py`
- [x] 做了：驗證 targeted tests 與 full pytest。
- [ ] 未做：處理其他 Flet/UI 類型 deprecation（原因：本顆只專修這批已定位的 warnings）。

---

## What was done

### 1. 問題來源確認
先依 pytest warning summary 反查，確認 61 warnings 全是同一類：
- `DeprecationWarning: If you wish to set the TextThemeStyle, use Text.theme_style instead. The Text.style property should be used to set the TextStyle only.`

### 2. 實際修法
把舊寫法：
- `ft.Text(..., style=ft.TextThemeStyle.XYZ)`

改成：
- `ft.Text(..., theme_style=ft.TextThemeStyle.XYZ)`

不改：
- 文字內容
- 色彩
- 權重
- UI layout
- 任何其他邏輯

### 3. 本次修正檔案
- `app/views/bundler_view.py`
- `app/views/config/config_form.py`
- `app/views/lookup_view.py`
- `app/views/qc_view.py`
- `app/views/rules_view.py`

---

## Important findings
- 這 61 個 warnings 本質上不是功能錯誤，而是 Flet API 過時提醒。
- 但如果不分開處理，容易跟主線 refactor 混成一團，之後很難判讀到底是行為改壞還是只是在清 deprecation。
- 抽成獨立 PR 後，驗證結果很乾淨：warnings summary 直接消失。

---

## Validation checklist
- [x] `rg -n "style\s*=\s*ft\.TextThemeStyle|style\s*=\s*TextThemeStyle" app/views app/views/config --glob "*.py"`（確認已全數改為 `theme_style`）
- [x] `uv run pytest -q tests/test_bundler_view_characterization.py tests/test_config_view_characterization.py tests/test_lookup_view_characterization.py tests/test_qc_view_characterization.py tests/test_rules_view_characterization.py --basetemp=.pytest-tmp\pr59 -o cache_dir=.pytest-cache\pr59 -W default`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr59-full -o cache_dir=.pytest-cache\pr59-full -W default`

## Test result
```text
$ uv run pytest -q tests/test_bundler_view_characterization.py tests/test_config_view_characterization.py tests/test_lookup_view_characterization.py tests/test_qc_view_characterization.py tests/test_rules_view_characterization.py --basetemp=.pytest-tmp\pr59 -o cache_dir=.pytest-cache\pr59 -W default
.................                                                        [100%]
17 passed in 0.90s

$ uv run pytest -q --basetemp=.pytest-tmp\pr59-full -o cache_dir=.pytest-cache\pr59-full -W default
........................................................................ [ 42%]
........................................................................ [ 84%]
...........................                                              [100%]
171 passed in 1.87s
```

---

## Rejected approach
1) 試過：把這批 deprecation 修補混進 PR40–58 主線一起做。
   - 為什麼放棄：這會把 UI cleanup 與主線結構 refactor 混在一起，之後很難判讀問題來源。
   - 最終改採：獨立開一顆 UI cleanup PR，單獨驗證、單獨收斂。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 UI layout
- 沒有改 view 行為
- 沒有處理 tkinter / CI workflow 問題
- 沒有處理其他 Flet 類型 deprecation

---

## Next step
- 若要讓 GitHub PR 完全綠燈，還需要另外處理 CI workflow（`setup-uv` 參數錯誤）與 Linux runner 的 tkinter 安裝問題。
- 但這兩題不屬於本顆 UI warnings cleanup 範圍。
