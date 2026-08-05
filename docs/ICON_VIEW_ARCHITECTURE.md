# Icon Preview 系統架構

## 定位

IconPreviewView（`app/views/icon_preview_view.py`，約 1750 行）是**翻譯校對頁**：載入 en_us（原文）＋ zh_tw（譯文）後，以「模組清單 → 單一模組詳情」兩層 UI 逐筆校對翻譯，並顯示每筆 key 對應的 mod icon。

支援兩種來源模式（`_detect_source_mode()`）：
- `jar_directory`：來源是 mods 資料夾（.jar 檔優先）— 直接讀 ZIP 內容，不改磁碟
- `extracted_folder`：傳統解包資料夾（存在 en_us.json）
- `empty`：無法識別

## 檔案結構

```
app/views/icon_preview_view.py        ← 主視圖（雙層 UI + 載入/搜尋/儲存）
app/icon_index.py                     ← JAR 模式：建立/快取 modid → icon 路徑索引
app/icon_reader.py                    ← JAR 模式：IconRef 解析 + 從 ZIP 讀 icon bytes
translation_tool/core/
  ├─ lang_item_row.py                 ← 單筆 key 列（icon 解析 + 翻譯輸入框）
  ├─ icon_resolver.py                 ← resolve_icon_with_reason() / resolve_icon_for_lang_key()
  ├─ icon_reason.py                   ← IconRisk / IconResult 資料結構
  ├─ icon_preview_cache.py            ← generate_icon_preview()（64×64 快取）
  └─ icon_classifier.py               ← ⚠️ 死程式碼：classify_no_icon_reason 無任何 caller
```

## 主要流程

```
[載入] _on_load_clicked()
  ├─ _detect_source_mode()
  │    ├─ extracted_folder → _load_entries()
  │    │      ├─ 掃 source_root 的 en_us.json → modid 清單
  │    │      ├─ zh_map：Track1 直接路徑（review_root/<modid>/lang/zh_tw.json）
  │    │      │          Track2 rglob 補漏（容錯）
  │    │      └─ 建立 entries（modid/key/en/zh_tw）
  │    └─ jar_directory → _load_entries_from_jar_directory()
  │           ├─ Phase 1/3：收集 modid（掃 JAR 內 lang/en_us.json）
  │           ├─ Phase 2/3：zh_tw 對照表（雙軌制）
  │           ├─ Phase 3/3：建立 entries + 批次提取 icon（_batch_extract_jar_icons）
  │           └─ L2 快取（_compute_cache_key + _load_entries_cache_l2 / _save_entries_cache_l2）
  ├─ _render_mod_list() → 第一層
  └─ 分頁 + 搜尋（_on_mod_search_change → debounce 300ms）

[校對] _open_mod_detail(modid) → 第二層
  ├─ _render_current_page()
  │    ├─ 依 _detail_filtered_entries（搜尋）或 mods[current_modid] 分頁
  │    └─ 每筆 → LangItemRow(key, en, zh, assets_root, preview_root, icon_path)
  ├─ 即時搜尋：_on_detail_search_change → debounce（_do_detail_search）
  └─ _on_value_changed(key, value) → _zh_data 更新

[儲存] _save_current_zh() → 寫入 zh_tw.json
```

## Icon 解析（LangItemRow 內）

1. `icon_path` 已提供（JAR 模式 pre-extract）→ 直接使用，跳過 resolve
2. 否則 `resolve_icon_with_reason(lang_key, assets_root)`（icon_resolver.py，含 LRU 快取）
3. `_HAS_ICON_READER` 且解析成功 → `IconRef.parse()` + `read_icon_bytes()`（從 ZIP 讀 bytes）→ 寫 preview_root 快取
4. `generate_icon_preview()`（icon_preview_cache.py）→ 64×64 PNG
5. 失敗 → 灰色 placeholder（不中斷 UI）

## JAR 模式 icon 提取（app/icon_index.py + view 內 helpers）

- `build_icon_index(mods_dir)` → `{key: jar://.../texture.png}` 索引，以 modpack hash 做版本快取（`_compute_modpack_hash` / `get_index_path` / `save_icon_index` / `load_icon_index`）
- `_try_extract_mod_icon_from_model(jar, modid, zf, names, key)`：解析 model JSON
  - model index 快取（`_load_model_index_from_cache` / `_build_model_index` / `_save_model_index_to_cache`）
  - 優先：用 key 轉 model name（`block.<modid>.<name>` → `block/<name>`）精準匹配
  - fallback：icon/logo/item_icon/block_icon 模型 — **僅當 key namespace 與 modid 一致時**（`block.minecraft.*` 等 vanilla namespace 不套用）
  - **不做 logo/icon.png 最終 fallback**（錯誤的 icon 比沒有更糟）
- `_extract_jar_icon` / `_batch_extract_jar_icons`：並行（ThreadPoolExecutor）批次提取並快取
- `_migrate_old_icon_cache`：舊版快取搬遷

## 主要 UI 元件

| 元件 | 說明 |
|------|------|
| `source_root` / `review_root` | 原文（en_us + textures）/ 校對（zh_tw）資料夾 |
| `mods` dict | modid → entries 列表 |
| `_entries_cache` / `_cache_meta` | L2 快取（source_root + mode 驗證） |
| `_mod_search_*` / `_detail_search_*` | 兩層即時搜尋（debounce Timer） |
| `LangItemRow` | 單筆 key：TextField（繁中可編輯）+ lang key + 英文原文 + icon 預覽 |

## 與舊版文件的差異（2026-08-05 確認）

舊版 ICON_VIEW_ARCHITECTURE 描述的流程（`_load_entries` → `LangItemRow.__init__` 直接 `resolve_icon_with_reason` + `classify_no_icon_reason` → IconRisk 標籤）**已過時**：

1. icon 解析核心仍走 `icon_resolver` / `icon_reason` / `icon_preview_cache`（由 `lang_item_row.py` 使用）✓
2. `icon_classifier.classify_no_icon_reason` **已無任何 caller**（死程式碼）
3. 新增 JAR 目錄模式（`app/icon_index.py` + `app/icon_reader.py` + `scan_jars`）與 L2 快取
4. 新增兩層即時搜尋（模組清單 / 詳情列）

## 維護注意

1. 新增 icon 解析策略時，優先改 `icon_index.py` / `icon_resolver.py`，不要塞進 view。
2. `_render_current_page` 每次重建 LangItemRow；entry 需帶 `icon_path` 避免重複解析。
3. 舊快取檔名以 SHA256 前 16 字元為 key；`_migrate_old_icon_cache` 負責搬遷。
4. `to_halfwidth()` 是全形轉半形工具（檔名正規化用）。
