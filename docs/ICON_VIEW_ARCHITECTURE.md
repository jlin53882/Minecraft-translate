# Icon Preview 系統架構

## 定位

IconPreviewView（`app/views/icon_preview_view.py`）是**翻譯校對頁**：載入 en_us（原文）＋ zh_tw（譯文）後，以「模組清單 → 單一模組詳情」兩層 UI 逐筆校對翻譯，並顯示每筆 key 對應的 mod icon。

支援的來源模式由 `_detect_source_mode()` 判斷（以 `source_root` 為準）：
- `jar_directory`：`source_root` 下有 `.jar`，且遞迴找不到任何 `en_us.json` — 直接讀 ZIP 內容，不改磁碟
- `extracted_folder`：遞迴找得到 `en_us.json`（傳統解包資料夾；即使同時有 .jar 也走此模式）
- `empty`：兩者皆無，無法識別（尚未設定 `source_root` 時回傳 `unknown`）

## 檔案結構

```
app/views/icon_preview_view.py        ← 主視圖（雙層 UI + 載入/搜尋/儲存）與 JAR icon 提取、L2 快取等模組層函式
app/icon_index.py                     ← 預建 icon 索引：load_icon_index() 供載入流程讀取
app/icon_reader.py                    ← IconRef 解析 + 從 ZIP 讀 icon bytes（read_icon_bytes）
app/views/icon_preview_row.py         ← LangItemRow：單筆 key 列（icon 解析 + 翻譯輸入框）
translation_tool/core/
  ├─ icon_resolver.py                 ← resolve_icon_with_reason() / resolve_icon_for_lang_key()
  ├─ icon_reason.py                   ← IconRisk / IconResult 資料結構
  ├─ icon_preview_cache.py            ← generate_icon_preview()（64×64 快取）
  └─ icon_classifier.py               ← classify_no_icon_reason()，由 icon_resolver.resolve_icon_with_reason 呼叫
```

## 畫面結構

`IconPreviewView.__init__` 依序呼叫 `_init_icon_preview_state`、`_init_icon_preview_paging_controls`、`_init_icon_preview_source_controls` 建立控制項，再組成 `controls`（由上而下）：

1. 標題列：`back_btn`（僅詳情頁顯示）、圖示、`header`
2. `mod_search_tf` / `mod_search_status`：模組清單搜尋（詳情頁隱藏）
3. 「資料來源」卡（`kit.section_card`）：`pick_source_btn` + `source_label`、`pick_review_btn` + `review_label`、`load_btn`、`progress_bar` / `progress_text`
4. `save_btn`（僅詳情頁顯示）
5. `page_bar`（`prev_page_btn` / `page_info` / `next_page_btn`）與 `page_size_selector`
6. `list_view`：模組清單列（`_mod_row`）或詳情頁的 `LangItemRow`

進入詳情頁時，`_update_detail_search_controls` 會在 `list_view` 前插入 `detail_search_tf` / `detail_search_status`。

## 主要流程

```
[載入] _on_load_clicked()
  ├─ _detect_source_mode()
  ├─ _try_use_cached_entries(mode)：L1（_entries_cache，source_root + mode 相符）→ L2（僅 jar_directory）→ 命中即 _rebuild_mods + _render_mod_list
  └─ 未命中 → _begin_scan(mode)
       ├─ 有 page.run_task：asyncio.to_thread(_scan_entries) 在背景執行緒掃描，完成後 _finish_load()
       └─ 否則同步 _scan_entries → _finish_load()
  _scan_entries(mode)
    ├─ extracted_folder → _load_entries()
    │      ├─ 掃 source_root 的 en_us.json（modid 取自路徑中 assets 的下一段）
    │      ├─ zh_map：Track1 直接路徑（review_root/<modid>/lang/zh_tw.json）
    │      │          Track2 rglob 補漏（容錯）
    │      └─ 建立 entries（modid/key/en/zh_tw）
    └─ jar_directory → _load_entries_from_jar_directory()
           ├─ _collect_jar_modids（Phase 1/3）：掃 JAR 內 lang/en_us.json 收集 modid
           ├─ _build_zh_tw_lookup（Phase 2/3）：zh_tw 對照表（雙軌制）
           ├─ _scan_jar_entries（Phase 3/3）：以 jar_browser.scan_jars 掃 en_us.json 建立 entries
           └─ _extract_and_cache_icons（Phase 4/4）：_batch_extract_jar_icons，再 _save_entries_cache_l2
  _finish_load()：寫 L1 快取、依 modid 分組為 mods、_render_mod_list()（第一層，依模組分頁；page_size_selector 控制每頁模組數）

[進度] _make_progress_callback / _show_progress_phase → 模組層 _set_progress → IconPreviewView._set_progress
  └─ 背景執行緒只記下最新值，_refresh_progress 節流（約 0.2 秒）後經 page.run_task 在 event loop 上套用到 progress_bar / progress_text

[校對] _open_mod_detail(modid) → 第二層
  ├─ 讀 zh_tw：review_root/<modid>/lang/zh_tw.json，不存在則 rglob 補找
  ├─ _render_current_page()
  │    ├─ 依 _detail_filtered_entries（搜尋）或 mods[current_modid] 分頁（每頁 page_size）
  │    └─ 每筆 → LangItemRow(lang_key, en_text, zh_text, assets_root, preview_root, on_value_changed, icon_path)
  ├─ 搜尋：_on_mod_search_change / _on_detail_search_change → Debouncer（150ms）→ _do_mod_search / _do_detail_search
  │    （詳情搜尋比對 key、英文、譯文）
  └─ _on_value_changed(key, value) → _zh_data 更新（to_halfwidth 正規化）

[儲存] _save_current_zh() → 寫入 _current_zh_file（找不到 zh_tw.json 時提示失敗）
```

## Icon 解析（LangItemRow 內）

1. `icon_path` 已提供（JAR 模式預先解析，可為 `jar://` URI）→ 直接包成 `IconResult`，跳過 resolve
2. 否則 `resolve_icon_with_reason(lang_key, assets_root)`（`assets_root` = `source_root / "assets"`）
3. `_resolve_preview_path`：`_HAS_ICON_READER` 且 `IconRef.parse()` 成功 → `read_icon_bytes()` 從 ZIP 讀 bytes 寫入 preview_root 快取；否則 `generate_icon_preview()`（64×64 PNG）
4. `_build_icon_widget`：`_ensure_icon_size` 將小於 64 的圖以 nearest neighbor 放大，顯示為 128×128 的 `ft.Image`
5. 無預覽圖：有 reason 時顯示灰底警示圖示 + 依 `IconRisk` 上色的原因文字；沒有 reason 則不顯示 icon（不中斷 UI）

## icon_resolver.py 決策鏈（extracted_folder 模式）

`resolve_icon_for_lang_key(lang_key, assets_root)` 的解析規則（不假設目錄結構）：

1. **取 key 末段**：`lang_key.split(".")[-1]`（如 `item.actuallyadditions.atomic_reconstructor` → `atomic_reconstructor`）
2. **取 modid**：`lang_key.split(".")[1]`（index 越界 → 回 None）
3. **定位 textures 根**：`assets/<modid>/textures`；不存在 → 回 None
4. **檔名比對**：`_build_icon_index(textures_root)` 以 **png 檔名（不含副檔名）為 key** 建索引（`rglob("*.png")`，重名保留第一個；`@lru_cache(128)` 以 textures root 為快取 key），再 `index.get(key_tail)`

`resolve_icon_with_reason(lang_key, assets_root)`：
- 命中 → `IconResult(icon_path=..., reason="", risk=None)`
- 未命中 → `classify_no_icon_reason(lang_key)`（icon_classifier.py，依 key 關鍵字啟發式分類）→ `IconResult(icon_path=None, reason, risk)`

## JAR 模式 icon 提取（view 模組層函式 + app/icon_index.py）

- `_batch_extract_jar_icons(jar_to_entries, icon_cache_root, source_root, progress_cb)`：
  1. 先 `icon_index.load_icon_index(source_root)`；有索引就直接套用（`_apply_icon_index`，不做 JAR I/O）
  2. 無索引則 `_run_jar_workers` 以 ThreadPoolExecutor（worker 數取 config `translator.parallel_execution_workers`，預設 4）逐 JAR 處理；只處理 `_key_needs_icon` 為真的 key（item/block/entity 等前綴）
- 逐 key 解析用 `_try_extract_mod_icon_from_model(jar, modid, zf, names, key)`：
  - model index 快取（`_load_model_index_from_cache` / `_build_model_index` / `_save_model_index_to_cache`，以 `_get_jar_hash`＝mtime+size 判斷失效）
  - 用 key 轉 model name（`block.<modid>.<name>` → `block/<name>`）精準匹配，並以 `_follow_parent_chain` 追 parent model 取 texture
  - 找不到或 key namespace 與 modid 不一致 → 回 None，**不做 logo/icon.png 最終 fallback**（錯誤的 icon 比沒有更糟）
- 索引檔位於 `.icon_cache/icon_index/`，以 JAR 檔名清單的 hash 命名；`icon_index.build_icon_index` / `save_icon_index` 目前沒有 app 內呼叫端，索引需事先建立
- `_extract_jar_icon`（含 icon.png / logo / NeoForge `logoFile` 等 fallback）目前沒有呼叫端
- `_migrate_old_icon_cache`：選擇模組資料夾時，把舊路徑 `<source_root>/_icon_preview/jar_icons/` 的 png 搬到 `.icon_cache/jar_icons/`

## 主要 UI 元件與狀態

| 元件 | 說明 |
|------|------|
| `source_root` / `review_root` | 原文（en_us + textures）/ 校對（zh_tw）資料夾 |
| `mods` dict | modid → entries 列表 |
| `_entries_cache` / `_cache_meta` | L1 快取（source_root + mode 驗證）；L2 為 `.icon_cache/<key>.json`，key 由 `_compute_cache_key`（JAR 檔名清單 hash）決定，僅 jar_directory 使用 |
| `_mod_search_*` / `_detail_search_*` | 兩層即時搜尋（`Debouncer`，150ms） |
| `mod_page_size` / `page_size` | 模組清單每頁數（`page_size_selector` 可選 25/50/100）/ 詳情頁每頁筆數（50） |
| `LangItemRow` | 單筆 key：TextField（繁中可編輯）+ lang key + 英文原文 + icon 預覽 |

## 維護注意

1. 新增 icon 解析策略時，優先改 `icon_index.py` / `icon_resolver.py`，不要塞進 view。
2. `_render_current_page` 每次重建 LangItemRow；entry 需帶 `icon_path` 避免重複解析。
3. L2 快取只看 JAR 檔名，JAR 內容改變但檔名不變不會自動失效。
4. `to_halfwidth()` 是全形轉半形（NFKC）工具，用於翻譯值正規化。
5. 背景掃描不可直接改控制項；進度一律走 `IconPreviewView._set_progress`。

## 分層邊界

`LangItemRow` 是 Flet UI 元件，位於 `app/views/icon_preview_row.py`；`translation_tool/core`
只保留 icon 解析與資料處理模組，不反向 import `app` 或 Flet。`IconPreviewView` 是 UI owner，
透過 `app.views.icon_preview_row` 建立列元件；核心的 `icon_resolver`、`icon_reason` 與
`icon_preview_cache` 維持可獨立測試的服務／資料層。新增 icon 行為時先改核心服務，只有控制項組裝
留在 row/view 層。
