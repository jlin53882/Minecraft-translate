"""測試 icon_resolver.py - 圖示解析邏輯。

用途：測試 resolve_icon_for_lang_key 和 resolve_icon_with_reason 函數。
"""

from translation_tool.core.icon_reason import IconRisk
from translation_tool.core.icon_resolver import (
    _build_icon_index,
    resolve_icon_for_lang_key,
    resolve_icon_with_reason,
)


class TestBuildIconIndex:
    """測試 _build_icon_index 函數"""

    def test_nonexistent_root_returns_empty_dict(self, tmp_path):
        """測試不存在的目錄應回傳空字典"""
        result = _build_icon_index(tmp_path / "not_exist")
        assert result == {}

    def test_builds_index_from_png_files(self, tmp_path):
        """測試從 PNG 檔案建立索引"""
        textures = tmp_path / "modid" / "textures"
        textures.mkdir(parents=True)

        (textures / "diamond_sword.png").write_bytes(b"fake png")
        (textures / "diamond_pickaxe.png").write_bytes(b"fake png")

        index = _build_icon_index(textures)

        assert "diamond_sword" in index
        assert "diamond_pickaxe" in index
        assert index["diamond_sword"].name == "diamond_sword.png"

    def test_nested_textures(self, tmp_path):
        """測試遞迴搜尋子目錄"""
        textures = tmp_path / "modid" / "textures"
        item_dir = textures / "item"
        item_dir.mkdir(parents=True)

        (item_dir / "apple.png").write_bytes(b"fake png")

        index = _build_icon_index(textures)
        assert "apple" in index


class TestResolveIconForLangKey:
    """測試 resolve_icon_for_lang_key 函數"""

    def test_nonexistent_assets_returns_none(self, tmp_path):
        """測試不存在的 assets 目錄應回傳 None"""
        assets_root = tmp_path / "assets"
        result = resolve_icon_for_lang_key("item.modid.test", assets_root)
        assert result is None

    def test_resolves_icon_correctly(self, tmp_path):
        """測試正確解析 icon 路徑"""
        assets_root = tmp_path / "assets"
        textures = assets_root / "modid" / "textures"
        textures.mkdir(parents=True)

        # 建立測試 icon
        icon_file = textures / "diamond_sword.png"
        icon_file.write_bytes(b"fake png")

        result = resolve_icon_for_lang_key("item.modid.diamond_sword", assets_root)

        assert result is not None
        assert result.name == "diamond_sword.png"

    def test_nested_texture_path(self, tmp_path):
        """測試子目錄中的 textures"""
        assets_root = tmp_path / "assets"
        textures = assets_root / "mymod" / "textures"
        item_dir = textures / "item"
        item_dir.mkdir(parents=True)

        (item_dir / "tool.png").write_bytes(b"fake png")

        result = resolve_icon_for_lang_key("item.mymod.tool", assets_root)
        assert result is not None
        assert result.name == "tool.png"

    def test_invalid_lang_key_returns_none(self, tmp_path):
        """測試無效的 lang key 格式"""
        assets_root = tmp_path / "assets"
        # 沒有分割點的 key
        result = resolve_icon_for_lang_key("invalidkey", assets_root)
        assert result is None


class TestResolveIconWithReason:
    """測試 resolve_icon_with_reason 函數"""

    def test_found_icon_returns_empty_reason(self, tmp_path):
        """測試找到 icon 時 reason 為空"""
        assets_root = tmp_path / "assets"
        textures = assets_root / "modid" / "textures"
        textures.mkdir(parents=True)

        (textures / "test.png").write_bytes(b"fake png")

        result = resolve_icon_with_reason("item.modid.test", assets_root)

        assert result.icon_path is not None
        assert result.reason == ""
        assert result.risk is None

    def test_not_found_returns_reason_and_risk(self, tmp_path):
        """測試找不到 icon 時回傳原因和風險"""
        assets_root = tmp_path / "assets"
        # 不建立任何 textures

        result = resolve_icon_with_reason("item.modid.nonexistent", assets_root)

        assert result.icon_path is None
        assert result.reason != ""
        assert result.risk is not None

    def test_banner_key_ignored(self, tmp_path):
        """測試 banner key 應被標記為 IGNORE"""
        assets_root = tmp_path / "assets"

        result = resolve_icon_with_reason("block.minecraft.banner.red", assets_root)

        assert result.icon_path is None
        assert result.risk == IconRisk.IGNORE
