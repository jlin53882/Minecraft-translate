"""測試 icon_preview_cache.py - 預覽快取邏輯。

用途：測試 generate_icon_preview 函數的圖示預覽生成功能。
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from translation_tool.core.icon_preview_cache import generate_icon_preview


class TestGenerateIconPreview:
    """測試 generate_icon_preview 函數"""

    def test_nonexistent_icon_returns_none(self, tmp_path):
        """測試不存在的圖示應回傳 None"""
        icon_path = tmp_path / "nonexistent.png"
        preview_root = tmp_path / "preview"

        result = generate_icon_preview(icon_path, preview_root)
        assert result is None

    def test_valid_png_creates_preview(self, tmp_path):
        """測試有效的 PNG 檔案應產生預覽"""
        # 建立一個簡單的測試 PNG
        icon_path = tmp_path / "test_icon.png"
        preview_root = tmp_path / "preview"

        # 寫入最小 PNG 檔案 (1x1 透明圖)
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
            b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        icon_path.write_bytes(png_data)

        result = generate_icon_preview(icon_path, preview_root)

        assert result is not None
        assert result.exists()
        assert result.suffix == ".png"
        assert "preview" in str(result)

    def test_cached_preview_returns_existing(self, tmp_path):
        """測試相同檔案應使用快取的預覽"""
        icon_path = tmp_path / "test_icon.png"
        preview_root = tmp_path / "preview"

        # 建立測試 PNG
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
            b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        icon_path.write_bytes(png_data)

        # 第一次呼叫
        result1 = generate_icon_preview(icon_path, preview_root)
        assert result1 is not None

        # 第二次呼叫應該回傳相同路徑（快取）
        result2 = generate_icon_preview(icon_path, preview_root)
        assert result1 == result2

    def test_invalid_image_returns_none(self, tmp_path, capsys):
        """測試無效的圖片應回傳 None 且不中斷"""
        icon_path = tmp_path / "invalid.png"
        preview_root = tmp_path / "preview"

        # 寫入無效資料
        icon_path.write_bytes(b"not an image")

        result = generate_icon_preview(icon_path, preview_root)

        assert result is None
        # 應該有警告訊息
        captured = capsys.readouterr()
        assert "WARN" in captured.out or "無法產生" in captured.out

    def test_preview_root_created_if_not_exists(self, tmp_path):
        """測試 preview_root 不存在時會自動建立"""
        icon_path = tmp_path / "test_icon.png"
        preview_root = tmp_path / "new_preview_dir"

        # 建立測試 PNG
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
            b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        icon_path.write_bytes(png_data)

        assert not preview_root.exists()

        result = generate_icon_preview(icon_path, preview_root)

        assert result is not None
        assert preview_root.exists()
