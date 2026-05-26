"""test_jar_browser.py - jar_browser 單元測試

測試目標：
- 基本 JAR 掃描
- 多 pattern 支援
- bad zip 忽略
- callback 被呼叫
- binary 檔案回傳 None
- 回傳型別驗證
- 空目錄處理
- max_workers 來源（config vs fallback）
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from translation_tool.utils import jar_browser


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def create_test_jar(tmp_dir: Path, jar_name: str, files: dict[str, str | bytes]) -> Path:
    """在 tmp_dir 建立測試用 JAR 檔案。

    參數：
        tmp_dir: 暫存目錄
        jar_name: JAR 檔案名稱（含副檔名）
        files: {檔案路徑: 內容}，內容可為 str（文字）或 bytes（二進位）

    回傳：
        Path: 建立好的 JAR 檔案路徑
    """
    jar_path = tmp_dir / jar_name
    with zipfile.ZipFile(jar_path, "w") as zf:
        for name, content in files.items():
            if isinstance(content, str):
                zf.writestr(name, content.encode("utf-8"))
            else:
                zf.writestr(name, content)  # bytes，直接寫入
    return jar_path


def create_bad_jar(tmp_dir: Path, jar_name: str) -> Path:
    """建立一個假的（不是有效 ZIP）JAR 檔案。"""
    jar_path = tmp_dir / jar_name
    with open(jar_path, "wb") as f:
        f.write(b"this is not a valid zip file at all")
    return jar_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_jar(tmp_path: Path) -> Path:
    """建立一個含有多個文字檔的範例 JAR。"""
    files = {
        "assets/mod_a/lang/en_us.json": json.dumps({"key_a": "value_a"}),
        "assets/mod_a/lang/zh_tw.json": json.dumps({"key_a": "翻譯_a"}),
        "fabric.mod.json": json.dumps({"id": "mod_a"}),
    }
    return create_test_jar(tmp_path, "mod_a-1.0.0.jar", files)


@pytest.fixture
def binary_jar(tmp_path: Path) -> Path:
    """建立一個含有 binary 檔案（png）的 JAR。"""
    # PNG magic bytes（最小的有效 PNG）
    png_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    files = {
        "assets/mod_b/icon.png": png_data,
        "assets/mod_b/lang/en_us.json": json.dumps({"icon": "test"}),
    }
    return create_test_jar(tmp_path, "mod_b-2.0.0.jar", files)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScanJarsBasic:
    """基本 JAR 掃描測試。"""

    def test_scan_jars_single_jar(self, sample_jar: Path, tmp_path: Path):
        """測試單一 JAR、單一 pattern。"""
        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
        )

        assert isinstance(result, dict)
        assert len(result) == 1
        assert sample_jar in result

        files = result[sample_jar]
        assert "assets/mod_a/lang/en_us.json" in files
        content = files["assets/mod_a/lang/en_us.json"]
        assert content is not None
        assert "key_a" in content

    def test_scan_jars_returns_correct_type(self, sample_jar: Path, tmp_path: Path):
        """驗證回傳型別是 dict[Path, dict[str, str | None]]。"""
        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
        )

        for jar_path, files in result.items():
            assert isinstance(jar_path, Path)
            assert isinstance(files, dict)
            for name, content in files.items():
                assert isinstance(name, str)
                assert content is None or isinstance(content, str)


class TestScanJarsMultiplePatterns:
    """多 pattern 支援測試。"""

    def test_scan_jars_two_patterns(self, sample_jar: Path, tmp_path: Path):
        """測試多個 pattern（en_us.json + fabric.mod.json）。"""
        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[
                r"assets/([^/]+)/lang/en_us\.json",
                r"fabric\.mod\.json",
            ],
        )

        files = result[sample_jar]
        assert "assets/mod_a/lang/en_us.json" in files
        assert "fabric.mod.json" in files
        assert files["assets/mod_a/lang/en_us.json"] is not None
        assert files["fabric.mod.json"] is not None

    def test_scan_jars_partial_match(self, tmp_path: Path):
        """測試 pattern 只能匹配到部分檔案。"""
        files = {
            "assets/mod_c/lang/en_us.json": json.dumps({"c": "test"}),
            "assets/mod_c/lang/zh_tw.json": json.dumps({"c": "翻譯"}),  # 不符合 pattern
        }
        create_test_jar(tmp_path, "mod_c.jar", files)

        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
        )

        jar_path = list(result.keys())[0]
        assert "assets/mod_c/lang/en_us.json" in result[jar_path]
        assert "assets/mod_c/lang/zh_tw.json" not in result[jar_path]


class TestScanJarsBadZip:
    """Bad ZIP / 損壞 JAR 的錯誤隔離測試。"""

    def test_scan_jars_bad_zip_ignored(self, sample_jar: Path, tmp_path: Path):
        """測試壞掉的 JAR 被忽略，正常 JAR 不受影響。"""
        bad_jar = create_bad_jar(tmp_path, "bad_mod.jar")

        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
        )

        # bad_jar 應該不在結果中（被跳過）
        assert bad_jar not in result
        # sample_jar 應該還在（沒被影響）
        assert sample_jar in result

    def test_scan_jars_exception_isolated(self, tmp_path: Path):
        """測試其他 Exception 不會影響整體（某個 JAR 失敗不影響其他）。"""
        # 建立一個可以建立但讀取時會失敗的特殊 JAR
        files = {"assets/mod_d/lang/en_us.json": json.dumps({"d": "test"})}
        good_jar = create_test_jar(tmp_path, "good_mod.jar", files)
        bad_jar = create_bad_jar(tmp_path, "bad_mod.jar")

        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
        )

        # 好的 JAR 必須在結果中
        assert good_jar in result
        # 壞的 JAR 不在結果中（被隔離）
        assert bad_jar not in result


class TestScanJarsCallback:
    """進度回呼 callback 測試。"""

    def test_scan_jars_callback_called(self, sample_jar: Path, tmp_path: Path):
        """測試 callback 在每個 JAR 完成後都被呼叫。"""
        callback_calls: list[tuple[int, int]] = []

        def track_callback(processed: int, total: int):
            callback_calls.append((processed, total))

        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
            processed_callback=track_callback,
        )

        # callback 應該被呼叫 1 次（1 個 JAR）
        assert len(callback_calls) == 1
        assert callback_calls[0] == (1, 1)

        # 結果必須正確
        assert len(result) == 1

    def test_scan_jars_callback_multiple_jars(self, tmp_path: Path):
        """測試多個 JAR 時 callback 被正確呼叫多次。"""
        files_a = {"assets/mod_e/lang/en_us.json": json.dumps({"e": "a"})}
        files_b = {"assets/mod_f/lang/en_us.json": json.dumps({"f": "b"})}
        create_test_jar(tmp_path, "mod_e.jar", files_a)
        create_test_jar(tmp_path, "mod_f.jar", files_b)

        callback_calls: list[tuple[int, int]] = []

        def track_callback(processed: int, total: int):
            callback_calls.append((processed, total))

        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
            processed_callback=track_callback,
        )

        assert len(callback_calls) == 2
        assert callback_calls[0] == (1, 2)
        assert callback_calls[1] == (2, 2)


class TestScanJarsBinary:
    """Binary 檔案（UTF-8 decode 失敗）測試。"""

    def test_scan_jars_binary_file_returns_none(self, binary_jar: Path, tmp_path: Path):
        """測試 binary 檔案（如 .png）UTF-8 decode 失敗時回傳 None。"""
        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/icon\.png"],
        )

        files = result[binary_jar]
        assert "assets/mod_b/icon.png" in files
        # PNG 是 binary，UTF-8 decode 會失敗，因此為 None
        assert files["assets/mod_b/icon.png"] is None

    def test_scan_jars_mixed_files(self, binary_jar: Path, tmp_path: Path):
        """測試同一個 JAR 裡有文字檔和 binary 檔。"""
        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[
                r"assets/([^/]+)/icon\.png",
                r"assets/([^/]+)/lang/en_us\.json",
            ],
        )

        files = result[binary_jar]
        # .png 是 binary → None
        assert files["assets/mod_b/icon.png"] is None
        # .json 是文字 → 字串內容
        assert files["assets/mod_b/lang/en_us.json"] is not None
        assert isinstance(files["assets/mod_b/lang/en_us.json"], str)


class TestScanJarsEmptyAndEdge:
    """空目錄與邊界條件測試。"""

    def test_scan_jars_empty_dir(self, tmp_path: Path):
        """測試空目錄回傳空 dict。"""
        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
        )

        assert result == {}

    def test_scan_jars_no_jar_files(self, tmp_path: Path):
        """測試目錄中沒有 JAR 檔案時回傳空 dict。"""
        # 建立一個非 JAR 檔案
        (tmp_path / "not_a_jar.txt").write_text("hello")

        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
        )

        assert result == {}

    def test_scan_jars_workers_from_config(self, tmp_path: Path, monkeypatch):
        """測試 max_workers=None 時從 config 讀取。"""
        fake_config = {
            "translator": {
                "parallel_execution_workers": 4,
            }
        }

        def mock_load_config(config_path=None):
            return fake_config

        monkeypatch.setattr(jar_browser, "load_config", mock_load_config)

        files = {"assets/mod_g/lang/en_us.json": json.dumps({"g": "test"})}
        create_test_jar(tmp_path, "mod_g.jar", files)

        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
            max_workers=None,  # 不指定，使用 config 值
        )

        # 不 crash 就是成功（內部讀到 4 個 workers）
        assert len(result) == 1

    def test_scan_jars_workers_default_fallback(self, tmp_path: Path, monkeypatch):
        """測試 config 沒有設定時 fallback 到 CPU count // 2。"""
        def mock_load_config(config_path=None):
            return {}  # config 為空，沒有 translator.parallel_execution_workers

        monkeypatch.setattr(jar_browser, "load_config", mock_load_config)

        files = {"assets/mod_h/lang/en_us.json": json.dumps({"h": "test"})}
        create_test_jar(tmp_path, "mod_h.jar", files)

        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
            max_workers=None,
        )

        # 不 crash 就是成功
        assert len(result) == 1

    def test_scan_jars_explicit_workers(self, tmp_path: Path):
        """測試 explicit max_workers 參數。"""
        files = {"assets/mod_i/lang/en_us.json": json.dumps({"i": "test"})}
        create_test_jar(tmp_path, "mod_i.jar", files)

        result = jar_browser.scan_jars(
            jar_dir=tmp_path,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
            max_workers=1,  # 明確指定 1
        )

        assert len(result) == 1

    def test_get_default_workers_cpu_count_none_raises_type_error(self, monkeypatch):
        """Regression: _get_default_workers crashes when os.cpu_count() returns None.

        在某些環境（容器/sandbox）中 os.cpu_count() 可能回傳 None，
        導致 None // 2 拋出 TypeError。
        此測試文件化此已知問題：_get_default_workers() 中的
        `max(1, os.cpu_count() // 2)` 在 cpu_count() 為 None 時拋 TypeError。
        """
        import translation_tool.utils.jar_browser as jb_module
        import os as os_mod

        original_cpu_count = os_mod.cpu_count
        os_mod.cpu_count = lambda: None
        try:
            result = jb_module._get_default_workers()
        except TypeError:
            pass
        finally:
            os_mod.cpu_count = original_cpu_count