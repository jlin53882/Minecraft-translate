# Release Strategy (Minecraft-translate)

## Goals
- 建立「可重複執行、可追溯」的 Release 流程
- 讓版本號、CHANGELOG、GitHub Release 三者對齊
- 讓 PR 合併後，自動由 CI 確保品質（pytest / lint / format）

---

## Versioning: Semantic Versioning (SemVer)
版本格式：`MAJOR.MINOR.PATCH`
- **MAJOR**：破壞性變更（使用者升級需要改設定/改用法）
- **MINOR**：向下相容的新功能 / 大重構（不改行為契約）
- **PATCH**：bugfix（不改 API/行為契約）

> 本專案目前仍在快速迭代階段，0.x 版允許較大重構，但仍建議用 SemVer 的語意去約束「什麼該升 minor、什麼該升 patch」。

---

## Proposed Milestone Tags
依你提供的 PR 範圍切版：
- **v0.6.0** → PR1 – PR39（建立可用產品線 + 大量重構/測試護欄）
- **v0.7.0** → PR40 – PR58（依 roadmap 進一步 pipeline/core/view 拆分；測試先行）
- **v0.8.0** → next feature improvements（新增功能，不破壞既有契約）
- **v1.0.0** → first stable release（對外介面/設定格式相對穩定、文件齊全、CI 成熟）

---

## Branch / Tag Policy
- `main`：永遠保持可跑（至少能 `uv run pytest -q`）
- Release 建議流程：
  1) 需要時開 `release/v0.6.x`（只收 bugfix）
  2) Release 內容 freeze 後，更新 `CHANGELOG.md` + `pyproject.toml` version
  3) 在該 commit 上打 tag：`v0.6.0`
  4) 推 tag：`git push origin v0.6.0`
  5) GitHub Release 用同一個 tag（可由 Actions 自動建立）

---

## Suggested Labeling for PRs (optional but recommended)
用 label 讓 release notes 更乾淨：
- `type:feature`
- `type:improvement`
- `type:refactor`
- `type:tests`
- `type:docs`
- `breaking`（若有）

---

## Definition of Done for a Release
- `CHANGELOG.md` 已補齊該版本區塊
- `pyproject.toml` version 已對齊 tag
- CI 綠燈（pytest + lint/format）
- GitHub Release 已建立（含 release notes）
