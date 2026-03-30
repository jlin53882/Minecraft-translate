# PR #52 設計文件：icon_preview 單元測試（補 PR #51）

> 狀態：規劃中
> 目的：為 PR #51 的實作內容補上單元測試，確保功能不被未來變更破壞

---

## 1. 測試策略

### 1.1 不使用真實 JAR 檔

測試使用 Python 標準庫 `zipfile` 和 `tempfile` 在記憶體/磁碟建立測試用的虛擬 JAR，避免依賴外部檔案。

```python
import zipfile, pathlib, tempfile

def create_test_jar(tmp_dir: pathlib.Path, jar_name: str, files: dict[str, str]) -> pathlib.Path:
    """建立測試用 JAR 檔案"""
    jar_path = tmp_dir / jar_name
    with zipfile.ZipFile(jar_path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return jar_path
```

### 1.2 Mock 範圍

- `page.overlay`：Mock Flet Page，避免 SnackBar 实际操作需要 Flet runtime
- `page.update()`：Mock 避免 UI 更新
- `source_picker`：Mock FilePicker

---

## 2. 測試檔一：模式偵測

### 2.1 測試檔

```
tests/test_icon_preview_mode_detection.py
```

### 2.2 測試案例

| 測試名 | 情境 | 預期結果 |
|--------|------|---------|
| `test_detect_jar_directory_mode` | JAR 檔存在、en_us.json 不存在 | `"jar_directory"` |
| `test_detect_extracted_folder_mode` | en_us.json 存在 | `"extracted_folder"` |
| `test_detect_empty_mode` | 兩者都沒有 | `"empty"` |
| `test_detect_unknown_mode` | source_root 為 None | `"unknown"` |

### 2.3 實作重點

```python
def test_detect_jar_directory_mode(tmp_path):
    """JAR 目錄模式偵測"""
    jar_dir = tmp_path / "mods"
    jar_dir.mkdir()
    (jar_dir / "mod1.jar").touch()
    (jar_dir / "mod2.jar").touch()

    view = IconPreviewView(page=MockPage())
    view.source_root = jar_dir

    mode = view._detect_source_mode()
    assert mode == "jar_directory"
```

---

## 3. 測試檔二：JAR 讀取邏輯

### 3.1 測試檔

```
tests/test_icon_preview_jar_entries.py
```

### 3.2 測試案例

| 測試名 | 情境 | 預期結果 |
|--------|------|---------|
| `test_load_entries_from_jar_directory_basic` | 單一 JAR，含 en_us.json | 回傳 entries |
| `test_load_entries_from_jar_directory_modid` | 從路徑正確解析 modid | modid 正確 |
| `test_load_entries_from_jar_directory_zh_tw` | 有 zh_tw 對照時 | zh_tw 有值 |
| `test_load_entries_from_jar_directory_bad_zip` | ZIP 格式錯誤 | 不拋錯，跳過該 JAR |
| `test_load_entries_from_jar_directory_progress` | 有 processed_callback | callback 被呼叫 |
| `test_load_entries_from_jar_directory_no_source_root` | source_root 為 None | 回傳空 list |
| `test_load_entries_from_jar_directory_no_jars` | 沒有 JAR 檔 | 回傳空 list |
| `test_load_entries_from_jar_directory_non_string_zh_tw` | zh_tw 是 list 而非 str | 回傳空字串 |

### 3.3 測試 JAR 結構

```python
def test_load_entries_from_jar_directory_basic(tmp_path):
    jar_dir = tmp_path / "mods"
    jar_dir.mkdir()

    create_test_jar(jar_dir, "actuallyadditions-1.20.jar", {
        "assets/actuallyadditions/lang/en_us.json": json.dumps({
            "item.actuallyadditions.atomic_reconstructor": "Atomic Reshaper",
            "item.actuallyadditions.manual": "Manual",
        }),
    })

    view = IconPreviewView(page=MockPage())
    view.source_root = jar_dir
    view.review_root = None  # 無 zh_tw

    entries = view._load_entries_from_jar_directory()

    assert len(entries) == 2
    assert entries[0].modid == "actuallyadditions"
    assert entries[0].key == "item.actuallyadditions.atomic_reconstructor"
    assert entries[0].en == "Atomic Reshaper"
    assert entries[0].zh_tw == ""  # 無 zh_tw
```

---

## 4. 測試檔三：雙軌 zh_tw 讀取

### 4.1 測試檔

```
tests/test_icon_preview_dual_track.py
```

### 4.2 測試案例

| 測試名 | 情境 | 預期結果 |
|--------|------|---------|
| `test_zh_tw_dual_track_direct` | 直接路徑存在 | 正確讀取 |
| `test_zh_tw_dual_track_rglob_fallback` | 直接路徑不存在，rglob 有 | 正確讀取 |
| `test_zh_tw_dual_track_defensive_non_string` | zh_tw 值是 list | 回傳空字串 |
| `test_zh_tw_dual_track_no_review_root` | review_root 為 None | zh_tw 回傳空字串 |

### 4.3 測試結構

```python
def test_zh_tw_dual_track_direct(tmp_path):
    jar_dir = tmp_path / "mods"
    review_dir = tmp_path / "review"
    assets_dir = review_dir / "assets" / "actuallyadditions" / "lang"
    assets_dir.mkdir(parents=True)

    # 寫入 zh_tw.json
    (assets_dir / "zh_tw.json").write_text(json.dumps({
        "item.actuallyadditions.atomic_reconstructor": "原子重塑器",
    }), encoding="utf-8")

    view = IconPreviewView(page=MockPage())
    view.review_root = review_dir

    # 驗證雙軌讀取
    modid = "actuallyadditions"
    direct = review_dir / modid / "lang" / "zh_tw.json"
    assert direct.exists()
```

---

## 5. 測試檔四：SnackBar Overlay 修復

### 5.1 測試檔

```
tests/test_icon_preview_snack_bar_fix.py
```

### 5.2 測試案例

| 測試名 | 情境 | 預期結果 |
|--------|------|---------|
| `test_snack_bar_inplace_modification` | 多次呼叫 SnackBar | overlay 長度保持穩定 |
| `test_snack_bar_deletes_only_snackbar` | overlay 有其他類型 | 只刪除 SnackBar，其他保留 |

### 5.3 實作重點

```python
def test_snack_bar_inplace_modification():
    """驗證 SnackBar 使用 in-place 修改（del），不使用賦值（=）"""
    page = MockPage()
    page.overlay = [ft.SnackBar(content=ft.Text("old"))]

    view = IconPreviewView(page=page)
    view._show_snack("new message", color=theme.WARNING)

    # overlay 裡只有一個 SnackBar（舊的被刪除了）
    snackbars = [o for o in page.overlay if isinstance(o, ft.SnackBar)]
    assert len(snackbars) == 1
    assert snackbars[0].content.value == "new message"
```

---

## 6. 測試檔五：快取讀寫（dict ↔ SimpleNamespace 轉換）

### 6.1 測試檔

```
tests/test_icon_preview_cache_transform.py
```

### 6.2 測試案例

| 測試名 | 情境 | 預期結果 |
|--------|------|---------|
| `test_cache_write_dict_from_namespace` | 寫入快取時 | SimpleNamespace 轉為 dict |
| `test_cache_read_dict_to_namespace` | 讀取快取時 | dict 轉回 SimpleNamespace |
| `test_cache_mixed_entries` | entries 同時有 dict 和 SimpleNamespace | 兩者都能正確處理 |

### 6.3 實作重點

```python
def test_cache_read_dict_to_namespace():
    """驗證快取讀回時 dict 轉回 SimpleNamespace"""
    page = MockPage()
    view = IconPreviewView(page=page)

    # 模擬快取內是 dict
    view._entries_cache = [
        {"modid": "actuallyadditions", "key": "item.actuallyadditions.atomic_reconstructor", "en": "Atomic Reshaper", "zh_tw": "原子重塑器"},
    ]
    view._cache_meta = {"source_root": "/fake/path", "mode": "jar_directory"}

    # 觸發快取復元
    view.source_root = Path("/fake/path")
    # ...（觸發 cache_valid = True 的條件）

    # 驗證：用屬性存取不應炸錯
    entry = view.mods["actuallyadditions"][0]
    assert entry.modid == "actuallyadditions"
    assert entry.zh_tw == "原子重塑器"
```

---

## 7. Mock 輔助工具

建立 `conftest.py` 共用 fixture：

```python
import pytest
from unittest.mock import MagicMock

@pytest.fixture
def mock_page():
    page = MagicMock()
    page.overlay = []
    page.update = MagicMock()
    return page

@pytest.fixture
def mock_view(mock_page):
    from app.views.icon_preview_view import IconPreviewView
    return IconPreviewView(page=mock_page)

def create_test_jar(tmp_path, jar_name, files):
    import zipfile
    jar_path = tmp_path / jar_name
    with zipfile.ZipFile(jar_path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return jar_path
```

---

## 8. 檔案變更

| 檔案 | 變更類型 |
|------|---------|
| `tests/test_icon_preview_mode_detection.py` | 新增 |
| `tests/test_icon_preview_jar_entries.py` | 新增 |
| `tests/test_icon_preview_dual_track.py` | 新增 |
| `tests/test_icon_preview_snack_bar_fix.py` | 新增 |
| `tests/test_icon_preview_cache_transform.py` | 新增 |
| `tests/conftest.py` | 修改（新增共用 fixture）|

---

## 9. 執行方式

```bash
cd C:\Users\admin\Desktop\minecraft_translator_flet
uv run pytest tests/test_icon_preview_mode_detection.py -v
uv run pytest tests/test_icon_preview_jar_entries.py -v
uv run pytest tests/test_icon_preview_dual_track.py -v
uv run pytest tests/test_icon_preview_snack_bar_fix.py -v
uv run pytest tests/test_icon_preview_cache_transform.py -v
```
