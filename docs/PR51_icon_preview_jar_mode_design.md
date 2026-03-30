# PR #51 設計文件：JAR 目錄模式 + 雙軌 zh_tw 讀取

> 狀態：已合併至 main（2026-03-30）
> 目的：記錄 PR #51 的實作內容，供未來審查與維護參考

---

## 1. 動機與目標

### 1.1 問題背景

原有的 `icon_preview_view` 只支援「已解包」的資料夾結構（`assets/modid/lang/en_us.json`）。使用者的 `mods` 資料夾內全是 JAR 檔，無法直接使用。

### 1.2 目標

- 不解包，直接從 JAR 檔讀取翻譯內容
- 對解包資料夾模式保持向後相容
- 每個按鈕都有 SnackBar feedback

---

## 2. 實作內容

### 2.1 模式偵測 `_detect_source_mode()`

```python
def _detect_source_mode(self) -> str:
    """偵測 source_root 是「JAR 目錄」還是「已解包資料夾」"""
    jar_count = len(list(self.source_root.glob("*.jar")))
    extracted_count = len(list(self.source_root.rglob("en_us.json")))

    if jar_count > 0 and extracted_count == 0:
        return "jar_directory"
    elif extracted_count > 0:
        return "extracted_folder"
    else:
        return "empty"
```

**邏輯**：
- `JAR > 0` 且 `en_us.json == 0` → JAR 目錄模式
- `en_us.json > 0` → 解包資料夾模式
- 兩者都沒有 → empty

### 2.2 JAR 目錄模式 `_load_entries_from_jar_directory()`

```python
def _load_entries_from_jar_directory(self, processed_callback=None) -> list:
    """從 JAR 目錄讀取所有 en_us.json（不改磁碟，直接讀 ZIP 內容）"""
```

**三階段流程**：

```
Phase 1：收集所有 modid
  for jar_path in jar_files:
      with zipfile.ZipFile(jar_path) as zf:
          for name in zf.namelist():
              if name.endswith("lang/en_us.json"):
                  modid = name.split("/")[1]
                  all_modids.add(modid)

Phase 2：建立 zh_tw 對照表（雙軌制）
  Track 1（直接路徑）：
      for modid in all_modids:
          path = review_root / modid / "lang" / "zh_tw.json"
          if path.exists():
              zh_map.update(data)

  Track 2（rglob fallback）：
      for zh_file in review_root.rglob("zh_tw.json"):
          if zh_file not in found_paths:
              zh_map.update(data)

Phase 3：建立 entries
  for jar_path in jar_files:
      with zipfile.ZipFile(jar_path) as zf:
          for name in zf.namelist():
              if name.endswith("lang/en_us.json"):
                  data = json.loads(zf.read(name))
                  for key, en_text in data.items():
                      zh_tw_raw = zh_map.get(key, "")
                      entries.append(SimpleNamespace(
                          modid=modid,
                          key=key,
                          en=en_text,
                          zh_tw=zh_tw_raw.strip(),
                          source_jar=jar_path.name,
                      ))
```

### 2.3 雙軌 zh_tw 對照表

同一套雙軌邏輯同時用於：
- `_load_entries()`（解包資料夾模式）
- `_load_entries_from_jar_directory()`（JAR 目錄模式）

| Track | 說明 | 優先級 |
|-------|------|--------|
| Track 1 | 直接路徑 `review_root/modid/lang/zh_tw.json` | 優先 |
| Track 2 | `rglob` 搜尋補漏 | fallback |

### 2.4 SnackBar 顯示機制

```python
def _show_snack(self, message: str, color=theme.WARNING):
    # 清除舊 SnackBar（in-place 修改，避免 Flet 0.28.3 的 overlay 無 setter 問題）
    for i in range(len(self.page.overlay) - 1, -1, -1):
        if isinstance(self.page.overlay[i], ft.SnackBar):
            del self.page.overlay[i]
    snack = ft.SnackBar(content=ft.Text(message), bgcolor=color, duration=3000)
    self.page.overlay.append(snack)
    snack.open = True
    self.page.update()
```

**已知問題與修復**：
- Flet 0.28.3 的 `page.overlay` 是唯讀屬性（無 setter）
- 不能用 `self.page.overlay = [...]` 賦值
- 需用 `del self.page.overlay[i]` in-place 修改

### 2.5 zh_tw 防禦機制

```python
zh_tw_raw = zh_map.get(key, "")
if not isinstance(zh_tw_raw, str):
    zh_tw_raw = ""
entries.append(SimpleNamespace(..., zh_tw=zh_tw_raw.strip()))
```

### 2.6 In-Memory 快取（L1）

```python
# 寫入快取
cache_entries = []
for entry in entries:
    if hasattr(entry, "__dict__"):
        cache_entries.append(entry.__dict__)  # SimpleNamespace → dict
    else:
        cache_entries.append(entry)
self._entries_cache = cache_entries
self._cache_meta = {"source_root": str(self.source_root), "mode": mode}

# 讀取快取
if cache_valid:
    mods = defaultdict(list)
    for entry in self._entries_cache:
        if isinstance(entry, dict):
            mods[entry["modid"]].append(SimpleNamespace(**entry))  # dict → SimpleNamespace
        else:
            mods[entry.modid].append(entry)
    self.mods = dict(mods)
    self._render_mod_list()
    return
```

**快取失效條件**：`source_root` 或 `mode` 改變。

### 2.7 ProgressBar 墊底 callback

在 JAR 掃描開始前先墊一次 callback，讓使用者知道掃描已啟動：
```python
if processed_callback:
    processed_callback()  # 墊底一次
```

---

## 3. 檔案變更

| 檔案 | 變更類型 |
|------|---------|
| `app/views/icon_preview_view.py` | 修改 |
| `.github/workflows/ci.yml` | 修改 |

---

## 4. 已發現但未實作的項目

| 項目 | 說明 | 歸屬 |
|------|------|------|
| 多執行緒 JAR 掃描 | 目前是同步 for 迴圈 | PR #53 |
| L2 磁碟快取 | 目前只有 in-memory | PR #53 |
| JAR icon 掃描 | 目前完全不支援 | PR #53 |
| LangItemRow icon_path | 目前沒有此參數 | PR #53 |
| icon 解析卡頓 | 每次換頁都重新 rglob | PR #53 |
| 單元測試 | 缺少 JAR 模式相關測試 | PR #55 |

---

## 5. 測試方式

1. 原文資料夾選 `mods`（全是 JAR）
2. 校對資料夾選 `lang_output/assets`
3. 點「載入模組清單」
4. 觀察 SnackBar + ProgressBar
5. 點任意模組 → 觀察 zh_tw 是否正確讀取
6. 編輯後點「儲存翻譯」
