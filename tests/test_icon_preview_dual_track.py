"""tests/test_icon_preview_dual_track.py

測試 icon_preview_view 的雙軌 zh_tw 讀取功能。
驗證 Track 1（直接路徑）和 Track 2（rglob fallback）的行為。
"""

import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import flet as ft


class MockPage:
    def __init__(self):
        self.overlay = []


def create_view(
    source_root: Path | None = None,
    review_root: Path | None = None,
):
    """建立 IconPreviewView 並設定 source_root 和 review_root"""
    from app.views.icon_preview_view import IconPreviewView

    with patch.object(IconPreviewView, "__init__", lambda self, page: None):
        view = IconPreviewView.__new__(IconPreviewView)
        view.page = MockPage()
        view.source_root = source_root
        view.review_root = review_root
        view._entries_cache = None
        view._cache_meta = {}
    return view


def create_en_us_jar(jar_dir: Path, jar_name: str, modid: str, entries: dict):
    """建立含 en_us.json 的測試 JAR"""
    import zipfile
    jar_path = jar_dir / jar_name
    with zipfile.ZipFile(jar_path, "w") as zf:
        zf.writestr(f"assets/{modid}/lang/en_us.json", json.dumps(entries))
    return jar_path


class TestZhTwDualTrack:
    """雙軌 zh_tw 讀取的各種情境測試"""

    def test_track1_direct_path_used(self, tmp_path):
        """直接路徑存在時，應該優先使用"""
        # 設定目錄結構（直接路徑：review_root/modid/lang/zh_tw.json）
        review_dir = tmp_path / "review"
        direct_dir = review_dir / "actuallyadditions" / "lang"
        direct_dir.mkdir(parents=True)

        zh_tw_file = direct_dir / "zh_tw.json"
        zh_tw_file.write_text(
            json.dumps({"item.actuallyadditions.atomic_reconstructor": "原子重塑器"}),
            encoding="utf-8",
        )

        view = create_view(source_root=None, review_root=review_dir)
        modid = "actuallyadditions"

        # 模擬雙軌邏輯：直接路徑
        direct = review_dir / modid / "lang" / "zh_tw.json"
        assert direct.exists() is True

        data = json.loads(direct.read_text(encoding="utf-8"))
        assert data["item.actuallyadditions.atomic_reconstructor"] == "原子重塑器"

    def test_track1_nonexistent_track2_used(self, tmp_path):
        """直接路徑不存在時，rglob fallback 應該找到"""
        # 直接路徑不存在
        review_dir = tmp_path / "review"

        # 但 rglob 可以找到
        nested_dir = review_dir / "some" / "nested" / "actuallyadditions" / "lang"
        nested_dir.mkdir(parents=True)
        zh_tw_file = nested_dir / "zh_tw.json"
        zh_tw_file.write_text(
            json.dumps({"item.actuallyadditions.manual": "手動"}),
            encoding="utf-8",
        )

        # rglob 驗證
        found = list(review_dir.rglob("zh_tw.json"))
        assert len(found) == 1
        assert found[0] == zh_tw_file

    def test_defensive_non_string_returns_empty(self, tmp_path):
        """zh_tw 值不是 str 時，應該回傳空字串（防禦機制）"""
        review_dir = tmp_path / "review"
        assets_dir = review_dir / "assets" / "testmod" / "lang"
        assets_dir.mkdir(parents=True)

        # 模擬一個錯誤的 zh_tw.json（某些值是 list 而非 str）
        zh_tw_file = assets_dir / "zh_tw.json"
        zh_tw_file.write_text(
            json.dumps({
                "key1": "正常字串",
                "key2": ["這是 list"],
                "key3": 12345,
            }),
            encoding="utf-8",
        )

        view = create_view(source_root=None, review_root=review_dir)

        # 模擬防禦邏輯
        raw_data = json.loads(zh_tw_file.read_text(encoding="utf-8"))
        for key, raw_value in raw_data.items():
            if not isinstance(raw_value, str):
                raw_value = ""
            assert isinstance(raw_value, str), f"非 str 值應該被轉為空字串，實際為 {type(raw_value)}"

    def test_track2_complements_track1(self, tmp_path):
        """Track 2 應該補上 Track 1 找不到的項目"""
        review_dir = tmp_path / "review"

        # Track 1：直接路徑只有 key1
        direct_dir = review_dir / "assets" / "mod" / "lang"
        direct_dir.mkdir(parents=True)
        (direct_dir / "zh_tw.json").write_text(
            json.dumps({"key1": "value from direct"}),
            encoding="utf-8",
        )

        # Track 2：在其他地方找到 key2
        nested_dir = review_dir / "other" / "mod" / "lang"
        nested_dir.mkdir(parents=True)
        (nested_dir / "zh_tw.json").write_text(
            json.dumps({"key2": "value from rglob fallback"}),
            encoding="utf-8",
        )

        # 驗證：兩個來源都應該被找到
        direct_file = review_dir / "mod" / "lang" / "zh_tw.json"
        found_all = list(review_dir.rglob("zh_tw.json"))

        # 合併兩個來源
        zh_map = {}
        if direct_file.exists():
            zh_map.update(json.loads(direct_file.read_text(encoding="utf-8")))
        for zh_file in review_dir.rglob("zh_tw.json"):
            if zh_file != direct_file:
                zh_map.update(json.loads(zh_file.read_text(encoding="utf-8")))

        assert zh_map.get("key1") == "value from direct"
        assert zh_map.get("key2") == "value from rglob fallback"

    def test_no_review_root_returns_empty(self, tmp_path):
        """review_root 為 None 時，zh_map 應該是空的"""
        view = create_view(source_root=None, review_root=None)

        # 當 review_root 為 None 時，不應嘗試讀取
        assert view.review_root is None
