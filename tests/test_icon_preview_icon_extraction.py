"""tests/test_icon_preview_icon_extraction.py

測試 icon_preview_view 的 icon 提取相關功能（P2 review 反饋）。

覆蓋：
- _extract_jar_icon()：單次 JAR icon 提取（fallback 順序、PNG 寫入、唯一性）
- _batch_extract_jar_icons()：批次 ZIP 處理（每 JAR 只開一次、progress callback、icon_path 回寫）
- _load_model_index_from_cache()：model index cache 讀取（hit / miss / 失效）
- _save_model_index_to_cache()：model index cache 寫入
"""

import pytest
import zipfile
import json
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch


# ==================================================
# 測試資料工廠
# ==================================================

def create_test_jar(jar_path: Path, files: dict[str, bytes]) -> None:
    """在 jar_path 建立測試用 JAR 檔案（parent dir auto-created）。"""
    jar_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(jar_path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def png_1x1() -> bytes:
    """回傳一個最小 1x1 白色 PNG（8 bytes）。"""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x00\x05\xfe\xd4\x00"
        b"\x00\x00\x00IEND\xaeB`\x82"
    )


def minimal_model_json(textures: dict) -> str:
    """建立最小 model JSON 字串（含 textures）。"""
    return json.dumps({
        "parent": "minecraft:item/generated",
        "textures": textures,
    })


# ==================================================
# _extract_jar_icon 測試
# ==================================================

class TestExtractJarIcon:
    """_extract_jar_icon 的各種情境測試。

    說明：Model JSON 解析（_try_extract_mod_icon_from_model）會寫入真實
    .icon_cache/model_index/ 目錄。在測試環境中直接 mock 該函式返回 None，
    隔離測試 _extract_jar_icon 的 fallback 邏輯。
    """

    def test_fallback_to_icon_png(self, tmp_path):
        """Model JSON 解析失敗時，fallback 到 icon.png"""
        from app.views.icon_preview_view import _extract_jar_icon

        jar = tmp_path / "test_mod-1.0.jar"
        cache_root = tmp_path / "icon_cache"
        create_test_jar(jar, {
            "assets/test_mod/icon.png": png_1x1(),
        })

        with patch("app.views.icon_preview_view._try_extract_mod_icon_from_model", return_value=None):
            result = _extract_jar_icon(jar, "test_mod", cache_root, "item.test_mod.hello")

        assert result is not None
        assert result.exists()
        assert result.suffix == ".png"
        assert result.read_bytes() == png_1x1()

    def test_fallback_to_logo_png(self, tmp_path):
        """icon.png 不存在時，fallback 到 logo.png"""
        from app.views.icon_preview_view import _extract_jar_icon

        jar = tmp_path / "test_mod-1.0.jar"
        cache_root = tmp_path / "icon_cache"
        create_test_jar(jar, {
            "assets/test_mod/textures/logo.png": png_1x1(),
        })

        # Model JSON 解析失敗（mock 返回 None），才會走到 logo.png fallback
        with patch("app.views.icon_preview_view._try_extract_mod_icon_from_model", return_value=None):
            result = _extract_jar_icon(jar, "test_mod", cache_root, "item.test_mod.hello")

        assert result is not None
        assert result.exists()
        assert result.read_bytes() == png_1x1()

    def test_neoforge_logofile_fallback(self, tmp_path):
        """NeoForge logoFile fallback"""
        from app.views.icon_preview_view import _extract_jar_icon

        jar = tmp_path / "neoforge_mod-1.0.jar"
        cache_root = tmp_path / "icon_cache"
        create_test_jar(jar, {
            "META-INF/neoforge.mods.toml": 'logoFile="assets/neoforge_mod/logo.png"'.encode(),
            "assets/neoforge_mod/logo.png": png_1x1(),
        })

        with patch("app.views.icon_preview_view._try_extract_mod_icon_from_model", return_value=None):
            result = _extract_jar_icon(jar, "neoforge_mod", cache_root, "item.neoforge_mod.hello")

        assert result is not None
        assert result.exists()

    def test_no_icon_returns_none(self, tmp_path):
        """找不到任何 icon 時回傳 None"""
        from app.views.icon_preview_view import _extract_jar_icon

        jar = tmp_path / "empty_mod-1.0.jar"
        cache_root = tmp_path / "icon_cache"
        create_test_jar(jar, {
            "assets/empty_mod/lang/en_us.json": b'{}',
        })

        result = _extract_jar_icon(jar, "empty_mod", cache_root, "item.empty_mod.hello")

        assert result is None

    def test_corrupted_zip_returns_none(self, tmp_path):
        """ZIP 損壞時不回報例外，回傳 None"""
        from app.views.icon_preview_view import _extract_jar_icon

        jar = tmp_path / "bad_mod-1.0.jar"
        cache_root = tmp_path / "icon_cache"
        jar.write_bytes(b"this is not a valid zip")

        result = _extract_jar_icon(jar, "bad_mod", cache_root, "item.bad_mod.hello")

        assert result is None

    def test_icon_path_unique_per_key(self, tmp_path):
        """同一個 modid，不同 key 產生不同的 icon 檔名"""
        from app.views.icon_preview_view import _extract_jar_icon

        jar = tmp_path / "test_mod-1.0.jar"
        cache_root = tmp_path / "icon_cache"
        create_test_jar(jar, {
            "assets/test_mod/icon.png": png_1x1(),
        })

        with patch("app.views.icon_preview_view._try_extract_mod_icon_from_model", return_value=None):
            path1 = _extract_jar_icon(jar, "test_mod", cache_root, "item.test_mod.one")
            path2 = _extract_jar_icon(jar, "test_mod", cache_root, "item.test_mod.two")

        assert path1 is not None
        assert path2 is not None
        assert path1 != path2  # 不同 key → 不同檔名

    def test_icon_cache_root_created_if_not_exists(self, tmp_path):
        """icon_cache_root 不存在時自動建立"""
        from app.views.icon_preview_view import _extract_jar_icon

        jar = tmp_path / "test_mod-1.0.jar"
        cache_root = tmp_path / "nonexistent_cache_dir"  # 不存在
        create_test_jar(jar, {
            "assets/test_mod/icon.png": png_1x1(),
        })

        with patch("app.views.icon_preview_view._try_extract_mod_icon_from_model", return_value=None):
            result = _extract_jar_icon(jar, "test_mod", cache_root, "item.test_mod.hello")

        assert result is not None
        assert cache_root.exists()


# ==================================================
# _batch_extract_jar_icons 測試
# ==================================================

class MockEntry:
    """模擬翻譯 entry（含 modid / key / icon_path）。"""
    def __init__(self, modid: str, key: str, icon_path: str | None = None):
        self.modid = modid
        self.key = key
        self.icon_path = icon_path


class TestBatchExtractJarIcons:
    """_batch_extract_jar_icons 的各種情境測試。"""

    def test_single_jar_opened_once(self, tmp_path):
        """同一個 JAR 的多個 entry 只開一次 ZIP"""
        from app.views.icon_preview_view import _batch_extract_jar_icons

        jar = tmp_path / "mods" / "test_mod-1.0.jar"
        jar.parent.mkdir(parents=True)
        create_test_jar(jar, {
            "assets/test_mod/icon.png": png_1x1(),
            "assets/test_mod/lang/en_us.json": b'{}',
        })

        entries = [
            MockEntry("test_mod", "item.test_mod.one"),
            MockEntry("test_mod", "item.test_mod.two"),
            MockEntry("test_mod", "item.test_mod.three"),
        ]
        jar_to_entries = {"test_mod-1.0.jar": entries}
        cache_root = tmp_path / "icon_cache"

        zip_open_count = 0
        original_zipfile = zipfile.ZipFile

        def counting_zipfile(*args, **kwargs):
            nonlocal zip_open_count
            zip_open_count += 1
            return original_zipfile(*args, **kwargs)

        with patch("zipfile.ZipFile", side_effect=counting_zipfile):
            with patch("app.views.icon_preview_view._try_extract_mod_icon_from_model", return_value=None):
                processed = _batch_extract_jar_icons(jar_to_entries, cache_root, tmp_path / "mods")

        assert processed == 1
        assert zip_open_count == 1, f"ZIP 應只開一次，實際：{zip_open_count}"

    def test_icon_path_written_back_to_entries(self, tmp_path):
        """找到 icon 時，icon_path 正確寫回 entry"""
        from app.views.icon_preview_view import _batch_extract_jar_icons

        jar = tmp_path / "mods" / "test_mod-1.0.jar"
        jar.parent.mkdir(parents=True)
        create_test_jar(jar, {
            "assets/test_mod/icon.png": png_1x1(),
        })

        entry1 = MockEntry("test_mod", "item.test_mod.one")
        entry2 = MockEntry("test_mod", "item.test_mod.two")
        jar_to_entries = {"test_mod-1.0.jar": [entry1, entry2]}
        cache_root = tmp_path / "icon_cache"

        with patch("app.views.icon_preview_view._try_extract_mod_icon_from_model", return_value=None):
            _batch_extract_jar_icons(jar_to_entries, cache_root, tmp_path / "mods")

        assert entry1.icon_path is not None
        assert entry2.icon_path is not None
        # 新行為：同 mod 同 icon，URI 共享（無磁碟寫入）
        assert entry1.icon_path == entry2.icon_path
        assert entry1.icon_path.startswith("jar://")
        assert entry1.icon_path.endswith(":assets/test_mod/icon.png")

    def test_missing_jar_skipped(self, tmp_path):
        """JAR 檔案不存在時跳過，不拋例外"""
        from app.views.icon_preview_view import _batch_extract_jar_icons

        entry = MockEntry("missing_mod", "item.missing_mod.hello")
        jar_to_entries = {"missing_mod-1.0.jar": [entry]}
        cache_root = tmp_path / "icon_cache"
        progress_calls = []

        def progress(processed, total):
            progress_calls.append((processed, total))

        processed = _batch_extract_jar_icons(jar_to_entries, cache_root, tmp_path / "mods", progress_cb=progress)

        assert processed == 1
        assert entry.icon_path is None  # 沒有寫入

    def test_progress_callback_invoked(self, tmp_path):
        """progress callback 每個 JAR 都會被呼叫"""
        from app.views.icon_preview_view import _batch_extract_jar_icons

        jar = tmp_path / "mods" / "mod_a-1.0.jar"
        jar.parent.mkdir(parents=True)
        create_test_jar(jar, {"assets/mod_a/lang/en_us.json": b"{}"})

        jar2 = tmp_path / "mods" / "mod_b-1.0.jar"
        create_test_jar(jar2, {"assets/mod_b/lang/en_us.json": b"{}"})

        entries = [MockEntry("mod_a", "item.mod_a.hello")]
        entries2 = [MockEntry("mod_b", "item.mod_b.hello")]
        jar_to_entries = {"mod_a-1.0.jar": entries, "mod_b-1.0.jar": entries2}
        cache_root = tmp_path / "icon_cache"
        progress_calls = []

        def progress(processed, total):
            progress_calls.append((processed, total))

        with patch("app.views.icon_preview_view._try_extract_mod_icon_from_model", return_value=None):
            _batch_extract_jar_icons(jar_to_entries, cache_root, tmp_path / "mods", progress_cb=progress)

        # 應該有 2 次 callback（每個 JAR 一次）
        assert len(progress_calls) >= 2

    def test_multiple_modids_same_jar(self, tmp_path):
        """同一個 JAR 含多個 modid，每個都嘗試提取"""
        from app.views.icon_preview_view import _batch_extract_jar_icons

        jar = tmp_path / "mods" / "multi_mod-1.0.jar"
        jar.parent.mkdir(parents=True)
        create_test_jar(jar, {
            "assets/mod_a/icon.png": png_1x1(),
            "assets/mod_b/icon.png": png_1x1(),
        })

        entries = [
            MockEntry("mod_a", "item.mod_a.hello"),
            MockEntry("mod_b", "item.mod_b.hello"),
        ]
        jar_to_entries = {"multi_mod-1.0.jar": entries}
        cache_root = tmp_path / "icon_cache"

        with patch("app.views.icon_preview_view._try_extract_mod_icon_from_model", return_value=None):
            _batch_extract_jar_icons(jar_to_entries, cache_root, tmp_path / "mods")

        assert entries[0].icon_path is not None
        assert entries[1].icon_path is not None


# ==================================================
# _load_model_index_from_cache 測試
# ==================================================

class TestLoadModelIndexFromCache:
    """_load_model_index_from_cache 的各種情境測試。"""

    def test_cache_miss_no_file(self, tmp_path):
        """cache 檔不存在 → 回傳 None"""
        from app.views.icon_preview_view import _load_model_index_from_cache

        jar = tmp_path / "test.jar"
        create_test_jar(jar, {})

        result = _load_model_index_from_cache(jar, "test_mod")

        assert result is None

    def test_cache_miss_wrong_modid(self, tmp_path):
        """cache 存在但 modid 不匹配 → 回傳 None"""
        from app.views.icon_preview_view import _load_model_index_from_cache

        jar = tmp_path / "test.jar"
        create_test_jar(jar, {})
        cache_dir = tmp_path / "model_index_cache"
        cache_dir.mkdir()
        # 建立 cache，modid 為 "wrong_modid"
        cache_file = cache_dir / "test.json"
        cache_file.write_text(json.dumps({
            "jar_name": jar.name,
            "jar_hash": "dummy_hash",
            "modid": "wrong_modid",
            "index": {"item/test": []},
        }))

        with patch("app.views.icon_preview_view._get_jar_hash", return_value="dummy_hash"):
            result = _load_model_index_from_cache(jar, "correct_modid")

        assert result is None

    def test_cache_hit(self, tmp_path):
        """cache 存在且 hash + modid 匹配 → 回傳 index"""
        from app.views.icon_preview_view import _load_model_index_from_cache

        jar = tmp_path / "test.jar"
        create_test_jar(jar, {})
        expected_index = {"item/test": ["models/item/test.json"]}

        def fake_cache_dir():
            cache_dir = tmp_path / "model_index_cache"
            cache_dir.mkdir()
            cache_file = cache_dir / "test.json"
            cache_file.write_text(json.dumps({
                "jar_name": jar.name,
                "jar_hash": "abc123",
                "modid": "test_mod",
                "index": expected_index,
            }))
            return cache_dir

        with patch("app.views.icon_preview_view._get_jar_hash", return_value="abc123"):
            with patch("app.views.icon_preview_view._get_model_index_cache_dir", side_effect=fake_cache_dir):
                result = _load_model_index_from_cache(jar, "test_mod")

        assert result == expected_index


# ==================================================
# _save_model_index_to_cache 測試
# ==================================================

class TestSaveModelIndexToCache:
    """_save_model_index_to_cache 的各種情境測試。"""

    def test_cache_file_written(self, tmp_path):
        """寫入後 cache 檔存在且內容正確"""
        from app.views.icon_preview_view import _save_model_index_to_cache

        jar = tmp_path / "test.jar"
        create_test_jar(jar, {})
        model_index = {"item/test": ["models/item/test.json"]}

        def fake_cache_dir():
            cache_dir = tmp_path / "model_index_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir

        with patch("app.views.icon_preview_view._get_jar_hash", return_value="xyz789"):
            with patch("app.views.icon_preview_view._get_model_index_cache_dir", side_effect=fake_cache_dir):
                _save_model_index_to_cache(jar, "test_mod", model_index)

        cache_dir = tmp_path / "model_index_cache"
        cache_file = cache_dir / "test.json"
        assert cache_file.exists()

        data = json.loads(cache_file.read_text())
        assert data["modid"] == "test_mod"
        assert data["jar_hash"] == "xyz789"
        assert data["index"] == model_index


# ==================================================
# to_halfwidth 純函式測試
# ==================================================

class TestToHalfwidth:
    """to_halfwidth 函式的各種情境測試。"""

    def test_全形數字轉半形(self):
        """全形數字轉半形"""
        from app.views.icon_preview_view import to_halfwidth
        assert to_halfwidth("１２３") == "123"

    def test_全形字母轉半形(self):
        """全形字母轉半形"""
        from app.views.icon_preview_view import to_halfwidth
        assert to_halfwidth("ＡＢＣ") == "ABC"

    def test_混合內容(self):
        """混合內容保留不需要轉換的部分"""
        from app.views.icon_preview_view import to_halfwidth
        assert to_halfwidth("Atomic Reshaper １２３") == "Atomic Reshaper 123"

    def test_空字串(self):
        """空字串不報錯"""
        from app.views.icon_preview_view import to_halfwidth
        assert to_halfwidth("") == ""

    def test_已全是半形(self):
        """已是半形的內容不變"""
        from app.views.icon_preview_view import to_halfwidth
        assert to_halfwidth("hello 123") == "hello 123"


# ==================================================
# _safe_filename_key 測試
# ==================================================

class TestSafeFilenameKey:
    """_safe_filename_key 的各種情境測試。"""

    def test_backslash_removed(self):
        """key 含反斜線時被移除"""
        from app.views.icon_preview_view import _safe_filename_key
        result = _safe_filename_key("Use \\locate structure betterjungletemples")
        assert "\\" not in result
        assert "locate" in result

    def test_normal_key(self):
        """正常 key 不變"""
        from app.views.icon_preview_view import _safe_filename_key
        assert _safe_filename_key("restonia_crystal_block") == "restonia_crystal_block"

    def test_slash_replaced(self):
        """斜線被替換為底線"""
        from app.views.icon_preview_view import _safe_filename_key
        result = _safe_filename_key("path/to/some_file")
        assert "/" not in result
        assert "some_file" in result

    def test_spaces_replaced(self):
        """空白被替換為底線"""
        from app.views.icon_preview_view import _safe_filename_key
        result = _safe_filename_key("some key with spaces")
        assert " " not in result
        assert "_" in result

    def test_long_key_truncated(self):
        """超長 key 被截斷"""
        from app.views.icon_preview_view import _safe_filename_key
        long_key = "a" * 100
        result = _safe_filename_key(long_key)
        assert len(result) <= 64

    def test_icon_generated_from_sanitized_key(self, tmp_path):
        """sanitized key 拿來當檔名時不應報錯"""
        from app.views.icon_preview_view import _extract_jar_icon, _safe_filename_key
        from app.views.icon_preview_view import _try_extract_mod_icon_from_model
        import zipfile

        # 建立含特殊字元的 key
        jar = tmp_path / "test_mod-1.0.jar"
        cache_root = tmp_path / "icon_cache"
        with zipfile.ZipFile(jar, "w") as zf:
            zf.writestr("assets/test_mod/icon.png", png_1x1())

        # 含反斜線的 key
        key_with_backslash = "Use \\locate structure"
        safe = _safe_filename_key(key_with_backslash)

        with patch("app.views.icon_preview_view._try_extract_mod_icon_from_model", return_value=None):
            result = _extract_jar_icon(jar, "test_mod", cache_root, key_with_backslash)

        assert result is not None, "含 \\ 的 key 應能產生 icon 檔"
        assert safe in result.name, f"icon 檔名應包含 sanitized key: {safe}"


# ==================================================
# atomic write 測試（tmp.replace）
# ==================================================

class TestAtomicWrite:
    """atomic write 使用 tmp.replace() 跨平台覆蓋目標檔案的測試。"""

    def test_overwrite_existing_cache_file(self, tmp_path):
        """寫入時目標檔案已存在，tmp.replace 應自動覆蓋不報錯"""
        from app.views.icon_preview_view import _save_model_index_to_cache
        import zipfile

        jar = tmp_path / "test.jar"
        with zipfile.ZipFile(jar, "w") as zf:
            zf.writestr("assets/test_mod/lang/en_us.json", b"{}")

        model_index = {"item/test": ["models/item/test.json"]}

        with patch("app.views.icon_preview_view._get_jar_hash", return_value="abc123"):
            with patch("app.views.icon_preview_view._get_model_index_cache_dir", return_value=tmp_path / "cache"):
                # 第一次寫入
                _save_model_index_to_cache(jar, "test_mod", model_index)
                # 第二次寫入（目標檔案已存在）
                _save_model_index_to_cache(jar, "test_mod", {"item/test2": ["models/item/test2.json"]})

        # 不應報錯，且檔案內容為最新
        cache_file = tmp_path / "cache" / "test.json"
        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert data["index"] == {"item/test2": ["models/item/test2.json"]}
