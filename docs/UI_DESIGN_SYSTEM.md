# UI 設計系統與外殼

「Deepslate & Emerald」深色為主、淺色完整對應。設計稿在 `docs/design/ui-redesign/`，
本文說明設計稿在程式碼中的落點。Flet 版本：1.0.1。

## 分層

| 層 | 位置 | 職責 |
|---|---|---|
| 設計 token / 主題 | `app/ui/design.py` | `Palette`（深 / 淺）、`ft.ColorScheme`、`build_theme()`、`apply(page, mode)`、語意色 `C`、語氣 `tone()` |
| UI kit | `app/ui/kit/` | 共用元件（見下表），頁面只組合 kit，不自己拼樣式 |
| 外殼 | `app/shell/` | `AppShell`、側欄、頂列、狀態列、快速跳轉、`TaskManager` |
| 頁面登錄 | `app/view_registry.py` | `ViewSpec`：標題、圖示、分組、快捷鍵、視窗尺寸，**唯一來源** |
| 設定存取 | `app/config_store.py` | 讀 / 寫 / 變更通知；View 不再直接 `load_config` |
| 任務模型 | `app/tasks/` | `TaskSession`、`LogEntry`（與 UI 無關，業務層可直接使用） |

## 色彩

顏色一律用 **語意字串**（`C.PANEL`、`C.EM`、`C.TEXT_DIM`…），由 Flet 依目前主題解析。
因此切換深 / 淺色只要 `design.apply(page, mode)`，**不需重建畫面**。

語氣（tone）：`em`（主色 / 成功）、`gold`（警告 / 進行中）、`dia`（資訊）、`ench`（強調）、`red`（錯誤）、`neutral`。
`design.tone(name)` 回傳 `fg / bg / line` 三色，Chip、StatCard、tone_icon 都用它。

主題模式存在設定檔 `ui.theme_mode`（`dark` / `light`），由 `AppShell.set_mode()` 經 `ConfigStore` 寫入。

## UI kit（`app/ui/kit/`）

| 模組 | 元件 |
|---|---|
| `basics` | `button`、`chip`、`count_badge`、`kbd`、`section_label`、`hint_text`、`tone_icon`、`vdivider`、`expand_kwargs`、`page_header` |
| `cards` | `SectionCard`、`StatCard`（`set_value`）、`ChoiceCard`、`StepCard`、`SwitchRow`、`Segmented`、`Pager` |
| `inputs` | `text_field`、`field`、`dropdown`、`pick_button` |
| `progress` | `ProgressRing`、`progress_bar` |
| `states` | `empty_state`、`loading_state`、`error_state` |
| `gallery` | 全部元件的目視對照頁（開發用） |

### Flet 1.0.1 注意事項

- `Row(wrap=True)` 的子項不能 `expand=False`，要用 `expand_kwargs()` 只在需要時才帶 `expand`。
- 在高度不受限的欄位裡，`Row(vertical_alignment=STRETCH)` 會讓內容消失，用 `START`。
- 控制項已有的欄位名（`key`、`page`…）不能當自訂屬性名（例如 `Pager.current_page`、`ChoiceCard.choice_key`）。
- 沒有 `InputDecorationTheme`，輸入框樣式由 `kit.field` / `kit.dropdown` 逐一套用。
- 捲軸為覆蓋式，右側要預留空間。

## 外殼

```
┌ Sidebar ┬──────────── TopBar（任務膠囊 · API 狀態 · 主題 · Ctrl+P）─┐
│ 分組導覽 │                       目前頁面                            │
│ (4 區+系統)├────────────────────── StatusBar ───────────────────────┤
└─────────┴───────────────────────────────────────────────────────────┘
```

- **ViewSpec / NAV_GROUPS**：分組為 流程 / 品檢 / 資料 / 輸出，加上 系統。快捷鍵 `Ctrl+1`…`Ctrl+0` 依導覽順序自動編號。
- **TaskManager**：訂閱 `TaskSession` 全域觀察者，頂列膠囊與狀態列顯示目前任務與進度，不需要頁面各自回報。
- **API 狀態**：`summarize_keys()` 讀取 `lm_key_health.snapshot()`（#113），顯示可用 / 冷卻中的金鑰數。
- **快速跳轉**：`Ctrl+P`，來源為 `AppShell.palette_items()`（頁面 + 動作）。
- 視窗：預設 1360×900，最小 1100×720。

## 新增頁面的步驟

1. 在 `view_registry.VIEW_SPECS` 加一筆 `ViewSpec`（並放進 `NAV_GROUPS`）。
2. 頁面繼承現有 View 基底，只用 `kit` 與語意色。
3. 需要設定時用 `ConfigStore`；需要長任務時用 `TaskSession`，TaskManager 會自動接上。
4. 在 `tests/test_views_redesign.py` 補結構測試；`tests/test_view_registry.py` 會檢查登錄表一致性。

## 真實畫面驗證

單元測試只驗證結構；真實 Flet 畫面、跨頁任務更新、深淺色與三組視窗尺寸由可重現的 Playwright harness 驗證：

```bash
uv run --isolated python tools/ui_smoke.py
```

完整矩陣、固定資料、CJK 字型、輸出位置與 baseline 接受流程見 [`UI_VISUAL_ACCEPTANCE.md`](UI_VISUAL_ACCEPTANCE.md)。效能量測契約見 [`PERFORMANCE.md`](PERFORMANCE.md)。生成的 screenshots 位於被 Git 忽略的 `.artifacts/`，不得直接提交進主線。
