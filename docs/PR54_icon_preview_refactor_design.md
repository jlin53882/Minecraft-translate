# PR #54 設計文件：icon_preview_view 重構（多執行緒 + L2 磁碟快取 + icon掃描）

> 狀態：✅ 已合併至 main（PR #55 / commit 9051498）
> 前提：PR #53（jar_browser.py）已合併

## 0. 目前狀態（2026-03-30 更新）

### icon 掃描功能：已實作，預設關閉

**實驗性參數**（`app/views/icon_preview_view.py` 第 24 行）：
```python
_ENABLE_JAR_ICON = False  # TODO: 找回 icon→key 的對應方式後啟用
```

**實作完成的內容**：
- ✅ `LangItemRow` 新增 `icon_path` 參數
- ✅ `_extract_jar_icon()` 支援：Fabric `assets/<modid>/icon.png`、Fabric `assets/<modid>/textures/**/*.png`、NeoForge `neoforge.mods.toml → logoFile`
- ✅ Phase 4 進度條（`ENABLE_JAR_ICON=True` 時生效）
- ✅ icon 重複覆蓋 bug 修復（每個 JAR 各自抓自己的 icon）
- ✅ 合併進 Phase 3 的 `scan_jars()` 流程

**未完成的內容**：
- ⚠️ icon → lang key 的對應關係尚未確認
  - icon 檔名不一定等於 lang key
  - `assets/<modid>/textures/gui/xxx.png` 這類路徑，後面的子路徑是否就是 key？
  - 需確認：icon 檔名 / 子路徑 → lang key 的映射邏輯

**Icon 搜尋順序**：
1. `assets/<modid>/icon.png`
2. `assets/<modid>/textures/**/*.png`（取第一個找到的）
3. NeoForge `logoFile`（從 `META-INF/neoforge.mods.toml` 解析）

**開啟方式**：將 `_ENABLE_JAR_ICON` 改為 `True`

---

## 1. 動機與目標

### 1.1 待解決問題

| 問題 | 說明 |
|------|------|
| **P0-1**：同步 JAR 掃描 | 目前 `_load_entries_from_jar_directory` 是同步 for 迴圈，500 JAR 需 60-150 秒 |
| **P0-2**：無 L2 磁碟快取 | 目前只有 in-memory cache（L1），關程式後完全重跑 |
| **P1-1**：JAR icon 未實作 | ✅ 已實作 `_ENABLE_JAR_ICON=False`，待找回 icon→key 對應後啟用 |
| **P1-2**：LangItemRow 無 icon_path | ✅ 已實作（`icon_path` 參數已加入）|
| **P1-3**：ProgressBar 無多 Phase | 使用者不知道內部在做什麼（收集 modid / 建立對照表 / 讀取翻譯）|

### 1.2 目標

- 用 `jar_browser.py` 取代同步 JAR 掃描
- 新增 L2 磁碟快取
- 實作 JAR icon 掃描（Fabric + NeoForge）✅ 已實作（預設關閉）
- 改造 `LangItemRow` 接收 `icon_path` ✅ 已實作
- 進度條顯示 Phase

---

## 2. 多執行緒 JAR 掃描（P0-1）

### 2.1 取代現有 `_load_entries_from_jar_directory`

```python
def _load_entries_from_jar_directory(self, processed_callback=None) -> list:
    """從 JAR 目錄讀取，使用 jar_browser 的多執行緒掃描"""
    from translation_tool.utils.jar_browser import scan_jars

    jar_files = list(self.source_root.glob("*.jar"))

    # Phase 1/2：收集 modid + 讀取 en_us.json（使用 jar_browser）
    results = scan_jars(
        jar_dir=self.source_root,
        patterns=[r"assets/([^/]+)/lang/en_us\.json"],
        processed_callback=processed_callback,
    )

    # 建立 zh_tw 對照表（雙軌）
    zh_map = self._build_zh_tw_map_dual_track()

    # 建立 entries
    entries = []
    for jar_path, files in results.items():
        for name, content in files.items():
            if not name.endswith("lang/en_us.json"):
                continue
            parts = name.split("/")
            modid = parts[1]
            data = json.loads(content)
            for key, en_text in data.items():
                zh_tw_raw = zh_map.get(key, "")
                if not isinstance(zh_tw_raw, str):
                    zh_tw_raw = ""
                entries.append(SimpleNamespace(
                    modid=modid,
                    key=key,
                    en=en_text,
                    zh_tw=zh_tw_raw.strip(),
                    source_jar=jar_path.name,
                ))
    return entries
```

### 2.2 進度條 Phase 顯示

修改 `processed_callback`，讓它支援 Phase 資訊：

```python
def _make_progress_callback(self, phase: str, total: int):
    def callback(processed: int, total: int):
        self.progress_text.value = f"[{phase}] {processed} / {total}"
        self.progress_bar.value = processed / total
        self.update()
    return callback

# 使用方式
results = scan_jars(
    jar_dir=self.source_root,
    patterns=[...],
    processed_callback=self._make_progress_callback("掃描中", total_steps),
)
```

---

## 3. L2 磁碟快取（P0-2）

### 3.1 快取存放位置

```python
import hashlib, platform

def _get_cache_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path.home() / ".cache"
    return base / "minecraft_translator" / "icon_preview"
```

### 3.2 快取 Key 計算

```python
def _compute_cache_key(source_root: Path) -> str:
    """計算快取 key：只看 JAR 檔案路徑，不算內容（避免每次都算 hash）"""
    jar_files = sorted([j.name for j in source_root.glob("*.jar")])
    key_str = str(source_root.resolve()) + ":" + ",".join(jar_files)
    return hashlib.sha256(key_str.encode()).hexdigest()[:16]
```

**注意**：key 只包含 JAR 的「檔名」，不包含路徑內容。這樣：
- 新增/移除 JAR → key 改變 → 快取失效
- JAR 內容變了但檔名不變 → **不會**自動失效（這是已知限制，未來可用 mtime 加強）

### 3.3 快取結構（含 versioning）

```json
{
  "version": 1,
  "source_root": "C:/Users/admin/.minecraft/versions/All the Mods 10/mods",
  "jar_manifest": ["a.jar", "b.jar"],
  "entries": [
    {
      "modid": "actuallyadditions",
      "key": "item.actuallyadditions.atomic_reconstructor",
      "en": "Atomic Reshaper",
      "zh_tw": "原子重塑器",
      "source_jar": "actuallyadditions-1.20.1.jar"
    }
  ],
  "created_at": "2026-03-30T20:00:00+08:00"
}
```

**version 欄位**：未來快取結構變更時，遞增 version，舊快取自動失效。

### 3.4 讀取快取 `_load_entries_cache_l2()`

```python
def _load_entries_cache_l2(self, mode: str) -> list | None:
    """讀取 L2 磁碟快取。回傳 None 表示快取失效。"""
    cache_dir = _get_cache_dir()
    cache_file = cache_dir / f"{self._compute_cache_key(self.source_root)}.json"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None  # 損壞的快取視為失效

    # 版本檢查
    if data.get("version") != 1:
        return None

    # JAR 數量檢查
    if data.get("source_root") != str(self.source_root):
        return None

    return data.get("entries", [])
```

### 3.5 寫入快取 `_save_entries_cache_l2()`

```python
def _save_entries_cache_l2(self, entries: list):
    """寫入 L2 磁碟快取（atomic write）"""
    import tempfile

    cache_dir = _get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{self._compute_cache_key(self.source_root)}.json"

    # Atomic write：用 tmp 檔再 rename
    tmp = cache_dir / f"{cache_file.stem}.tmp"
    data = {
        "version": 1,
        "source_root": str(self.source_root),
        "entries": [e.__dict__ if hasattr(e, "__dict__") else e for e in entries],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp.write_text(orjson.dumps(data).decode(), encoding="utf-8")
    tmp.rename(cache_file)  # POSIX atomic on most systems
```

### 3.6 快取分層流程

```
使用者點「載入模組清單」
        │
        ▼
   L1 快取命中？
    ├─ 是 → 直接用 self._entries_cache，零 IO
    └─ 否 → 繼續
        │
        ▼
   L2 快取命中？
    ├─ 是 → 從磁碟讀取，< 1 秒
    └─ 否 → 繼續
        │
        ▼
   L3：實際多執行緒掃描（jar_browser + ThreadPoolExecutor）
        │
        ▼
   結果寫入 L2（L1 也同時寫入）
```

---

## 4. JAR Icon 掃描（P1-1）

### 4.1 Fabric icon 解析

```python
def _find_fabric_icon(jar_path: Path, modid: str) -> str | None:
    """從 fabric.mod.json 解析 icon 路徑"""
    with zipfile.ZipFile(jar_path, 'r') as zf:
        # 找 fabric.mod.json
        fabric_meta = None
        for name in zf.namelist():
            if name == "fabric.mod.json":
                fabric_meta = json.loads(zf.read(name).decode("utf-8"))
                break

        if not fabric_meta:
            return None

        icon_path = fabric_meta.get("icon")
        if not icon_path:
            return None

        # icon_path 格式："assets/<modid>/icon.png"
        # 確認檔案存在
        full_path = icon_path  # 保持 JAR 內路徑格式
        if full_path in zf.namelist():
            return full_path
        return None
```

### 4.2 NeoForge / Forge logoFile 解析

```python
def _find_neoforge_icon(jar_path: Path) -> str | None:
    """從 neoforge.mods.toml 解析 logoFile

    ⚠️ 注意：neoforge.mods.toml 的位置不一定在 META-INF/ 目錄下，
    需以 zf.namelist() 搜尋 "neoforge.mods.toml" 為準，而非假設固定路徑。
    實作前請先用真實 JAR 驗證所有 neoforge 版本的路徑一致性和 logoFile 格式。
    """
    with zipfile.ZipFile(jar_path, 'r') as zf:
        namelist_lower = {n.lower(): n for n in zf.namelist()}
        neoforge_key = namelist_lower.get("neoforge.mods.toml")
        if not neoforge_key:
            return None

        content = zf.read(neoforge_key).decode("utf-8")
        for line in content.splitlines():
            if line.strip().startswith("logoFile="):
                logo = line.split("=", 1)[1].strip().strip('"')
                # logo 可能是相對路徑，確認在 JAR 內存在
                if logo in zf.namelist():
                    return logo
                return None
    return None
```

### 4.3 Icon 掃描整合進 `_load_entries_from_jar_directory`

在建立 entries 時同時建立 `modid → icon_path` 的映射：

```python
# 在 Phase 2 之前，先用 jar_browser 掃描 metadata
metadata_results = scan_jars(
    jar_dir=self.source_root,
    patterns=[r"fabric\.mod\.json", r"META-INF/neoforge\.mods\.toml"],
)

# 建立 modid → icon_path 映射
modid_icon_map: dict[str, str] = {}
for jar_path, files in metadata_results.items():
    fabric = files.get("fabric.mod.json")
    neoforge = files.get("META-INF/neoforge.mods.toml")
    # ... 解析邏輯 ...
    if icon_path:
        modid_icon_map[modid] = icon_path
```

---

## 5. LangItemRow 改造（P1-2）

### 5.1 新增 icon_path 參數

```python
class LangItemRow(ft.Container):
    def __init__(
        self,
        *,
        lang_key: str,
        en_text: str,
        zh_text: str,
        assets_root: Path,
        preview_root: Path,
        on_value_changed: Callable[[str, str], None],
        icon_path: str | None = None,  # 新增參數
    ):
        super().__init__(...)
        self.lang_key = lang_key
        self.on_value_changed = on_value_changed

        # icon 處理：優先用傳入的 icon_path，否則 fallback 到磁碟解析
        if icon_path:
            # icon_path 是 JAR 內路徑，需要從 JAR 讀取 binary
            self._display_icon_from_jar(icon_path)
        else:
            # Fallback：嘗試從磁碟解析（傳統 extracted_folder 模式）
            icon_result = resolve_icon_with_reason(lang_key, assets_root)
            if icon_result.icon_path:
                preview_path = generate_icon_preview(icon_result.icon_path, preview_root)
                ...
```

### 5.2 JAR Icon 顯示

```python
def _display_icon_from_jar(self, jar_path: str, icon_jar_path: Path):
    """從 JAR 讀取 icon 並顯示（用 base64 編碼）"""
    import base64, io
    from flet import Image, Container

    with zipfile.ZipFile(icon_jar_path, 'r') as zf:
        raw = zf.read(jar_path)

    # 轉為 base64 data URL 給 Flet Image
    b64 = base64.b64encode(raw).decode()
    data_url = f"data:image/png;base64,{b64}"

    self.icon_img = Image(src=data_url, width=128, height=128)
```

---

## 6. ProgressBar 多 Phase 顯示（P1-3）

### 6.1 Phase 設計

| Phase | 顯示文字 | 說明 |
|-------|---------|------|
| 1/3 | `收集模組資訊...` | 掃描 JAR 數量 |
| 2/3 | `建立翻譯對照表...` | 建立 zh_tw map |
| 3/3 | `讀取翻譯內容 12/500` | jar_browser 實際讀取 |

### 6.2 Phase 切換時機

```python
def _load_entries_from_jar_directory(self, processed_callback=None):
    total_jars = len(list(self.source_root.glob("*.jar")))

    # Phase 1/3
    self._show_progress_phase("收集模組資訊...", 0, total_jars)

    # Phase 2/3
    self._show_progress_phase("建立翻譯對照表...", 0, 1)
    zh_map = self._build_zh_tw_map_dual_track()
    self._show_progress_phase("建立翻譯對照表...", 1, 1)

    # Phase 3/3
    self._show_progress_phase("讀取翻譯內容", 0, total_jars)
    results = scan_jars(..., processed_callback=processed_callback)
```

---

## 7. 單元測試

> ⚠️ 注意：`test_icon_preview_snack_bar_fix.py` 已在 PR #52 實作，PR #54 若需修改該測試，應以更新（update）而非新增（create）。

| 測試檔 | 測試數 | 說明 |
|--------|-------|------|
| `test_icon_preview_l2_cache.py` | 6 | L2 快取命中 / miss / version / atomic write |
| `test_icon_preview_icon_scan.py` | 4 | Fabric / NeoForge icon 解析 |
| `test_lang_item_row_icon_path.py` | 3 | icon_path 參數行為 |
| `test_icon_preview_jar_browser_integration.py` | 3 | 確認使用 jar_browser |

---

## 8. 依賴

| 模組 | 用途 |
|------|------|
| `orjson` | L2 快取 atomic write（取代 `json`，效能更好）|
| `os` | CPU 核心數偵測（`os.cpu_count()`）|
| `pathlib` | 路徑操作 |
| `hashlib` | SHA256 快取 key 計算 |
| `zipfile` | JAR icon 讀取 |
| `translation_tool.utils.jar_browser` | PR #53 的多執行緒 JAR 掃描 |

---

## 9. 檔案變更

| 檔案 | 變更類型 |
|------|---------|
| `app/views/icon_preview_view.py` | 修改 |
| `translation_tool/core/lang_item_row.py` | 修改 |
| `tests/test_icon_preview_l2_cache.py` | 新增 |
| `tests/test_icon_preview_icon_scan.py` | 新增 |
| `tests/test_lang_item_row_icon_path.py` | 新增 |
| `tests/test_icon_preview_jar_browser_integration.py` | 新增 |

---

## 10. 已知限制

| 限制 | 說明 | 未來改善方向 |
|------|------|-------------|
| L2 快取只看 JAR 檔名 | JAR 內容變了但檔名不變時，快取不會自動失效 | 加入 JAR mtime 比對 |
| Icon 讀取有延遲 | JAR icon 用 base64 轉 data URL，大 icon 可能慢 | 改用路徑而非 base64 |
| progress_callback Phase 切換時機 | 目前 Phase 2 是同步的，時間很短 | 可考慮非同步化 |
