# GH_WORKFLOW.md

本檔是本 repo 的 GitHub / GitHub CLI（`gh`）標準作業流程（SOP）。

目標：
- 開 PR / 發 Release 時「描述一致、格式一致、可複製貼上」
- 避免再發生 Token 直接貼在聊天、或文字編碼變成 `???` 的事故

> 注意：本檔屬於 repo 長期治理文件，放在 `docs/`（不放 `docs/pr/`）。

---

## 0) 一次性環境設定（每台機器只做一次）

### 0.1 安裝 GitHub CLI
- Windows 建議用 winget：
  - `winget install --id GitHub.cli -e`

### 0.2 登入（建議使用 web flow，不要手動貼 PAT 到聊天）
```powershell
& "C:\Program Files\GitHub CLI\gh.exe" auth login -h github.com -p https -w
```

### 0.3 確認登入狀態
```powershell
& "C:\Program Files\GitHub CLI\gh.exe" auth status -h github.com
```

### 0.4 設定預設 repo（可選但強烈建議）
```powershell
& "C:\Program Files\GitHub CLI\gh.exe" repo set-default jlin53882/Minecraft-translate
```

---

## 1) 開 PR 標準流程

### 1.1 你要提供給我（每次開 PR 前）
- `--head`：你的分支名（例：`feat/pr40-lm-translator-split`）
- `--base`：目標分支（預設 `main`）
- PR 目的（這顆 PR 做什麼）
- Changelog（有哪些改動；可貼 commit log 或條列）

### 1.2 觸發詞約定
你只要說：
- 「**幫我開 PR**」→ 我會產出：PR body（Markdown）+ 指令

### 1.3 你執行（PowerShell 模板）

#### Step A：寫入 PR body（避免編碼亂掉）
```powershell
Set-Content -Path "$env:TEMP\pr-body.md" -Value @"
<我產出的 PR 描述>
"@ -Encoding UTF8
```

#### Step B：建立 PR
```powershell
& "C:\Program Files\GitHub CLI\gh.exe" pr create `
  --base main `
  --head <你的分支名> `
  --title "<PR title>" `
  --body-file "$env:TEMP\pr-body.md"
```

#### Step C：查看 PR 與 CI
```powershell
& "C:\Program Files\GitHub CLI\gh.exe" pr view --web
& "C:\Program Files\GitHub CLI\gh.exe" run list --limit 10
```

---

## 2) Release 標準流程（手動發版，gh 直接建立 Release）

### 2.1 你要提供給我（每次發 Release 前）
- 版本號（例：`v0.6.1`）
- 這版重點（或貼 CHANGELOG 區塊）
- 目標分支 / commit（預設 `main`）

### 2.2 觸發詞約定
你只要說：
- 「**幫我發 release vX.Y.Z**」→ 我會產出：release notes（Markdown）+ 指令

### 2.3 你執行（PowerShell 模板）

#### Step A：寫入 Release notes
```powershell
Set-Content -Path "$env:TEMP\release-notes.md" -Value @"
<我產出的 release notes>
"@ -Encoding UTF8
```

#### Step B：建立 Release
```powershell
& "C:\Program Files\GitHub CLI\gh.exe" release create `
  v<版本號> `
  --title "v<版本號> - <簡短描述>" `
  --notes-file "$env:TEMP\release-notes.md" `
  --target main
```

#### Step C：確認 Release
```powershell
& "C:\Program Files\GitHub CLI\gh.exe" release view v<版本號> --web
```

---

## 3) CI / Actions 監看（常用）

```powershell
& "C:\Program Files\GitHub CLI\gh.exe" run list --limit 20
& "C:\Program Files\GitHub CLI\gh.exe" run view <run-id> --log
```

---

## 4) 安全注意事項（硬規則）
- **不要**把 GitHub token 直接貼在 Discord/聊天。
- 若不得不用 PAT：只給單一 repo + 最小權限，用完 revoke。
- 文字內容用檔案（`--body-file` / `--notes-file`）傳給 gh，避免 encoding 變 `???`。

---

## 5) 與本 repo Release 治理文件的關係
- 發版的『版本語意/里程碑切法』→ 看 `RELEASE_STRATEGY.md`
- 發版的『流程步驟（含 tag / CHANGELOG / 版本 bump）』→ 看 `docs/RELEASE_WORKFLOW.md`
