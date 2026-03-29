# PR60：CI workflow fixes

## Summary
這顆 PR 只修兩個明確阻塞 CI 的問題，不混其他 cleanup：
1. `astral-sh/setup-uv@v3` 的錯誤輸入參數 `python-version`
2. Ubuntu runner 缺少 `tkinter`，導致 `bundler_view.py` / `qc_view.py` 在 pytest collect/import 階段直接炸掉

---

## Phase 1 完成清單
- [x] 做了：將 Python 版本管理改回 `actions/setup-python@v5`
- [x] 做了：`setup-uv@v3` 改成只負責 uv，自身不再接收錯誤的 `python-version` input
- [x] 做了：在 `test` job 補上 `python3-tk`
- [x] 做了：保留 `lint` job 不安裝 tkinter，避免額外放大 job scope
- [ ] 未做：處理 Node 20 deprecation / cache 400（原因：這兩條不是本顆 PR 的核心阻塞）

---

## What was done

### 1. 修正 setup-uv 用法
原本 workflow 把：
- Python 版本選擇
- uv 安裝

混在同一顆 action 內，而且 `astral-sh/setup-uv@v3` 收到了不支援的 input：
- `python-version`

現在改成：
- `actions/setup-python@v5` → 負責 Python 3.12
- `astral-sh/setup-uv@v3` → 只負責 uv + cache

### 2. 補上 Ubuntu runner 的 tkinter
`app/views/bundler_view.py` 與 `app/views/qc_view.py` 都會在 import 時直接使用：
- `import tkinter as tk`
- `from tkinter import filedialog`

所以 Linux runner 若沒裝 `python3-tk`，pytest 在 collect/import test module 階段就會直接失敗。

本次在 `test` job 補上：
- `sudo apt-get update && sudo apt-get install -y python3-tk`

### 3. 為什麼 lint job 沒跟著裝 tkinter
這顆 PR 目標是先把 test job 的阻塞點解除。

`lint` job 本身跑的是：
- `ruff check`
- `ruff format --check`

它不需要真的 import Flet/tkinter 模組去執行 UI 測試，因此這顆先不額外擴大 lint job 的安裝範圍。

---

## Important findings
- 這次 CI fail 並不是「單純缺 tkinter」而已。
- 真正的主因有兩層：
  1. workflow 寫錯：`setup-uv@v3` 不接受 `python-version`
  2. Linux runner 缺 tkinter，導致某些 view 在 import 階段失敗
- 所以正確修法必須是：
  - 先把 workflow action 分工修正
  - 再補 tkinter

---

## Validation checklist
- [x] `rg -n "setup-python|python-version|setup-uv|python3-tk|Install Tkinter" .github/workflows/ci.yml`
- [x] `git diff -- .github/workflows/ci.yml`

## Test result
```text
$ rg -n "setup-python|python-version|setup-uv|python3-tk|Install Tkinter" .github/workflows/ci.yml
16:      - uses: actions/setup-python@v5
18:          python-version: '3.12'
21:        uses: astral-sh/setup-uv@v3
25:      - name: Install Tkinter
26:        run: sudo apt-get update && sudo apt-get install -y python3-tk
44:      - uses: actions/setup-python@v5
46:          python-version: '3.12'
49:        uses: astral-sh/setup-uv@v3

$ git diff -- .github/workflows/ci.yml
(確認 test/lint job 均已改用 actions/setup-python@v5；test job 已補 python3-tk；setup-uv 不再帶錯誤 input)
```

---

## Rejected approach
1) 試過：只補 `python3-tk`，不修 workflow action 寫法。
   - 為什麼放棄：這只能解掉 `tkinter` import 問題，但 `setup-uv` 的 `Unexpected input(s) 'python-version'` 還是會讓 CI 爆掉。
   - 最終改採：先修 `setup-python` / `setup-uv` 的分工，再補 `python3-tk`。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有處理 Node 20 deprecation warning
- 沒有處理 cache restore 400
- 沒有調整 lint job 的執行策略
- 沒有改任何 Python 應用程式碼

---

## Next step
- 推上遠端後，應直接觀察 GitHub Actions 結果。
- 若 CI 還有剩餘問題，再根據新失敗訊息做下一輪最小修補，不預設一次把所有平台噪音都混進來。
