# PR60 Follow-up：release workflow 同步修正

## 補充原因
PR60 初版只修了 `.github/workflows/ci.yml`，但後續檢查發現：
- `.github/workflows/release.yml` 也仍在使用 `astral-sh/setup-uv@v3` + 錯誤 input `python-version`

這會造成：
- 即使 `ci.yml` 修好了，release workflow 仍可能在相同位置炸掉
- annotations 容易只顯示 job 名稱，讓人誤判成「只有 CI workflow 沒修好」

## 本次補充修正
- `release.yml` 新增 `actions/setup-python@v5`
- `setup-uv@v3` 改成只保留 `enable-cache: true`
- release workflow 同步補上 `python3-tk`

## 為什麼要同步補 tkinter
release workflow 也會在 Ubuntu runner 上跑：
- `uv sync --frozen`
- `uv run pytest -q`

而專案裡：
- `bundler_view.py`
- `qc_view.py`

在 import 階段就會依賴 tkinter，所以 release job 也需要同樣修補。
