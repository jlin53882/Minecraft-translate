# RELEASE_WORKFLOW.md

本檔描述本 repo 的「人工可控」Release 流程（先建立流程，再視需要自動化）。

## TL;DR
- 更新 `pyproject.toml` 版本號
- 更新 `CHANGELOG.md`
- 確認 CI 綠燈（pytest + ruff）
- 打 tag `vX.Y.Z`
- 推 tag
- 建 GitHub Release（可用 Actions 自動建立）

---

## Step-by-step

### 0) 確認主線狀態
```bash
uv run pytest -q
```

### 1) 決定版本號
- 有破壞性變更 → bump MAJOR
- 有新功能/大重構但相容 → bump MINOR
- 純 bugfix → bump PATCH

### 2) 更新版本號
檔案：`pyproject.toml`

### 3) 更新 CHANGELOG
檔案：`CHANGELOG.md`
- 新增該版本區塊
- 把 Unreleased 內容搬進去

### 4) 建立 release commit
```bash
git checkout -b release/v0.6.0
# edit files

git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): v0.6.0"
```

### 5) 確認 CI/測試
```bash
uv run pytest -q
# 若有 ruff：
uv run ruff check .
uv run ruff format --check .
```

### 6) Merge 回 main + 打 tag
```bash
git checkout main
git pull

# merge release branch (via PR recommended)

git tag -a v0.6.0 -m "v0.6.0"
git push origin v0.6.0
```

### 7) GitHub Release
- Title: Minecraft Translator v0.6
- Body: `release_notes_v0.6.0.md` 的內容（或從 CHANGELOG 擷取）

---

## Notes
- 若使用 uv：建議 CI 採 `uv sync --frozen`，確保與 `uv.lock` 完全一致。
- 若未來要自動化：可在 tag push 時由 GitHub Actions 自動建立 Release（見 `.github/workflows/release.yml`）。
