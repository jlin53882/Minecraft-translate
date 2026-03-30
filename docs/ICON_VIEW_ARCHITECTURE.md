# Icon Preview 系統架構

## 定位
隸屬 **Merge 階段**（第3步）：翻譯完成後，用於校對譯文與對照原版 icon 的視覺化介面。

## 系統組成

| 模組 | 用途 | 主要函式 |
|------|------|---------|
| `icon_classifier.py` | 启发式分類「無 icon」的原因 | `classify_no_icon_reason(lang_key)` → 回傳 (原因, IconRisk) |
| `icon_reason.py` | 定義診斷結果的資料結構 | `IconRisk` 列舉（IGNORE/WARN/DANGER）、`IconResult` 資料類別 |
| `icon_resolver.py` | 將 lang key 解析為實際 icon 圖檔路徑 | `resolve_icon_for_lang_key()`、`resolve_icon_with_reason()`（含快取） |
| `icon_preview_cache.py` | 將原始 icon 放大快取為 64×64 PNG | `generate_icon_preview(icon_path, preview_root)` |
| `icon_preview_view.py` | Flet 雙層 UI：模組清單 → 翻譯校對 | `IconPreviewView` 繼承 `ft.Column`，含分頁與儲存 |

## Icon 流程

```
翻譯完成（zh_tw.json + en_us.json）
        │
        ▼
IconPreviewView._load_entries()
  → 掃 en_us.json，逐 key 建立 LangItemRow
        │
        ▼
  LangItemRow.__init__
    → resolve_icon_with_reason(key, assets_root)
        ├── resolve_icon_for_lang_key() → 查詢 _build_icon_index()（LRU 快取）
        └── 查無時 → classify_no_icon_reason() → IconRisk 等級
        │
        ▼
    → generate_icon_preview(icon_path, preview_root)
        ├── SHA256 前 16 字元作為快取檔名
        └── 失敗則回傳 None（不中斷 UI）
        │
        ▼
    Flet Image(64×64) 或 灰色 placeholder + 風險標籤
        │
        ▼
使用者校對翻譯 → _on_value_changed → _zh_data[key] 更新
        │
        ▼
_save_current_zh() → 寫入 zh_tw.json
```

## IconPreviewView 架構

```
IconPreviewView (ft.Column)
├── 第一層：模組清單（_render_mod_list）
│   ├── ListTile × N（modid、總數、未翻譯數）
│   └── 分頁控制（prev / page_info / next）
│
└── 第二層：單一模組詳情（_open_mod_detail）
    ├── back_btn / header
    ├── LangItemRow × N（核心 UI 元件）
    │   ├── 左側：ft.Image（icon 預覽 128×128）或灰色 placeholder
    │   └── 右側：ft.Column
    │       ├── TextField（繁中翻譯，可編輯）
    │       ├── TextField（lang key，唯讀）
    │       ├── TextField（英文原文，唯讀）
    │       └── Text（風險提示或 icon 解析失敗警告）
    ├── page_bar（翻譯項目分頁）
    └── save_btn → _save_current_zh() 寫入 zh_tw.json
```
