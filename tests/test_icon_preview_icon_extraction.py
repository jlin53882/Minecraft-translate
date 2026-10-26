"""tests/test_icon_preview_icon_extraction.py

測試 icon_preview_view 的 icon 提取相關功能。

覆蓋：圖示解析（catalog／model／blockstate 挑選規則）與 ``to_halfwidth``。
逐 JAR 掃描（每 JAR 只開一次、icon_path 回寫）見 ``tests/test_icon_jar_scan.py``。
"""

import json
import zipfile
from pathlib import Path

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


# ==================================================
# 圖示解析測試
# ==================================================


class TestCatalogIconResolution:
    """catalog 圖示解析的各種情境測試。"""

    def test_catalog_uses_item_model_for_block_key(self, tmp_path):
        from app.views.icon_preview.icon_cache import (
            ModpackAssetCatalog,
            _resolve_icon_from_catalog,
        )

        mods = tmp_path / "mods"
        jar = mods / "securitycraft.jar"
        model = "assets/securitycraft/models/item/block_pocket_manager.json"
        texture = "assets/securitycraft/textures/block/manager.png"
        create_test_jar(
            jar,
            {
                model: json.dumps(
                    {"textures": {"layer0": "securitycraft:block/manager"}}
                ).encode(),
                texture: png_1x1(),
            },
        )
        catalog = ModpackAssetCatalog.from_mods_directory(mods)
        try:
            with zipfile.ZipFile(jar) as zf:
                result = _resolve_icon_from_catalog(
                    jar,
                    "securitycraft",
                    "block.securitycraft.block_pocket_manager",
                    catalog,
                    current_zf=zf,
                    budget=None,
                )
        finally:
            catalog.close()

        assert result == ("securitycraft:block/manager", texture, jar.resolve())

    def test_catalog_reads_default_minecraft_texture_from_client_jar(self, tmp_path):
        from app.views.icon_preview.icon_cache import (
            ModpackAssetCatalog,
            _resolve_icon_from_catalog,
        )

        version_dir = tmp_path / "ATM-Test"
        mods = version_dir / "mods"
        jar = mods / "example.jar"
        client_jar = version_dir / "ATM-Test.jar"
        texture = "assets/minecraft/textures/block/acacia_planks.png"
        create_test_jar(
            jar,
            {
                "assets/example/models/block/reinforced_planks.json": json.dumps(
                    {"textures": {"all": "block/acacia_planks"}}
                ).encode(),
            },
        )
        create_test_jar(client_jar, {texture: png_1x1()})
        catalog = ModpackAssetCatalog.from_mods_directory(mods)
        try:
            with zipfile.ZipFile(jar) as zf:
                result = _resolve_icon_from_catalog(
                    jar,
                    "example",
                    "block.example.reinforced_planks",
                    catalog,
                    current_zf=zf,
                    budget=None,
                )
        finally:
            catalog.close()

        assert result == ("block/acacia_planks", texture, client_jar.resolve())

    def test_catalog_resolves_foreign_key_namespace_parent_alias_and_texture_jar(
        self, tmp_path
    ):
        from app.views.icon_preview.icon_cache import (
            ModpackAssetCatalog,
            _resolve_icon_from_catalog,
        )

        mods = tmp_path / "mods"
        addon = mods / "bibliobiomes.jar"
        dependency = mods / "regions_unexplored.jar"
        texture = "assets/biomesoplenty/textures/block/dead_planks.png"
        create_test_jar(
            addon,
            {
                "assets/bibliocraft/models/item/biomesoplenty_dead_bookcase.json": json.dumps(
                    {
                        "parent": "bibliocraft:block/wood/biomesoplenty_dead/bookcase",
                        "textures": {"layer0": "#texture", "texture": "#face"},
                    }
                ).encode(),
                "assets/bibliocraft/models/block/wood/biomesoplenty_dead/bookcase.json": json.dumps(
                    {"textures": {"face": "biomesoplenty:block/dead_planks"}}
                ).encode(),
            },
        )
        create_test_jar(dependency, {texture: png_1x1()})
        catalog = ModpackAssetCatalog.from_mods_directory(mods)
        try:
            with zipfile.ZipFile(addon) as zf:
                result = _resolve_icon_from_catalog(
                    addon,
                    "bibliobiomes",
                    "block.bibliocraft.biomesoplenty_dead_bookcase",
                    catalog,
                    current_zf=zf,
                    budget=None,
                )
        finally:
            catalog.close()

        assert result == (
            "biomesoplenty:block/dead_planks",
            texture,
            dependency.resolve(),
        )

    def test_catalog_uses_blockstate_model_variant(self, tmp_path):
        from app.views.icon_preview.icon_cache import (
            ModpackAssetCatalog,
            _resolve_icon_from_catalog,
        )

        mods = tmp_path / "mods"
        jar = mods / "example.jar"
        texture = "assets/example/textures/block/stage.png"
        create_test_jar(
            jar,
            {
                "assets/example/blockstates/staged_block.json": json.dumps(
                    {"variants": {"": {"model": "example:block/stage_0"}}}
                ).encode(),
                "assets/example/models/block/stage_0.json": json.dumps(
                    {"textures": {"all": "example:block/stage"}}
                ).encode(),
                texture: png_1x1(),
            },
        )
        catalog = ModpackAssetCatalog.from_mods_directory(mods)
        try:
            with zipfile.ZipFile(jar) as zf:
                result = _resolve_icon_from_catalog(
                    jar,
                    "example",
                    "block.example.staged_block",
                    catalog,
                    current_zf=zf,
                    budget=None,
                )
        finally:
            catalog.close()

        assert result == ("example:block/stage", texture, jar.resolve())

    def test_catalog_uses_unique_texture_basename_for_moved_dependency_texture(
        self, tmp_path
    ):
        from app.views.icon_preview.icon_cache import (
            ModpackAssetCatalog,
            _resolve_icon_from_catalog,
        )

        mods = tmp_path / "mods"
        addon = mods / "addon.jar"
        dependency = mods / "dependency.jar"
        actual_texture = "assets/othermod/textures/block/wood_variant.png"
        create_test_jar(
            addon,
            {
                "assets/addon/models/item/wood_variant.json": json.dumps(
                    {"textures": {"layer0": "missingmod:item/wood_variant"}}
                ).encode(),
            },
        )
        create_test_jar(dependency, {actual_texture: png_1x1()})
        catalog = ModpackAssetCatalog.from_mods_directory(mods)
        try:
            with zipfile.ZipFile(addon) as zf:
                result = _resolve_icon_from_catalog(
                    addon,
                    "addon",
                    "item.addon.wood_variant",
                    catalog,
                    current_zf=zf,
                    budget=None,
                )
        finally:
            catalog.close()

        assert result == (
            "missingmod:item/wood_variant",
            actual_texture,
            dependency.resolve(),
        )

    def test_catalog_includes_exact_neoforge_runtime_parent_model(self, tmp_path):
        from app.views.icon_preview.icon_cache import (
            ModpackAssetCatalog,
            _resolve_icon_from_catalog,
        )

        minecraft = tmp_path / ".minecraft"
        profile_dir = minecraft / "versions" / "TestPack"
        mods = profile_dir / "mods"
        addon = mods / "actuallyadditions.jar"
        loader_version = "21.1.251"
        loader_jar = (
            minecraft
            / "libraries"
            / "net"
            / "neoforged"
            / "neoforge"
            / loader_version
            / f"neoforge-{loader_version}-universal.jar"
        )
        client_jar = profile_dir / "TestPack.jar"
        texture = "assets/minecraft/textures/item/bucket.png"
        create_test_jar(
            addon,
            {
                "assets/actuallyadditions/models/item/canola_oil_bucket.json": json.dumps(
                    {
                        "parent": "neoforge:item/bucket",
                        "fluid": "actuallyadditions:canola_oil",
                        "loader": "neoforge:fluid_container",
                    }
                ).encode(),
            },
        )
        create_test_jar(
            loader_jar,
            {
                "assets/neoforge/models/item/bucket.json": json.dumps(
                    {
                        "parent": "neoforge:item/default",
                        "textures": {
                            "base": "item/bucket",
                            "fluid": "neoforge:item/mask/bucket_fluid",
                        },
                    }
                ).encode(),
                "assets/neoforge/models/item/default.json": b"{}",
            },
        )
        create_test_jar(client_jar, {texture: png_1x1()})
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "TestPack.json").write_text(
            json.dumps(
                {"arguments": {"game": ["--fml.neoForgeVersion", loader_version]}}
            ),
            encoding="utf-8",
        )

        catalog = ModpackAssetCatalog.from_mods_directory(mods)
        try:
            with zipfile.ZipFile(addon) as zf:
                result = _resolve_icon_from_catalog(
                    addon,
                    "actuallyadditions",
                    "item.actuallyadditions.canola_oil_bucket",
                    catalog,
                    current_zf=zf,
                    budget=None,
                )
        finally:
            catalog.close()

        assert result == ("item/bucket", texture, client_jar.resolve())

    def test_catalog_rejects_ambiguous_texture_basename_fallback(self, tmp_path):
        from app.views.icon_preview.icon_cache import (
            ModpackAssetCatalog,
            _resolve_icon_from_catalog,
        )

        mods = tmp_path / "mods"
        addon = mods / "addon.jar"
        first = "assets/first/textures/item/shared.png"
        second = "assets/second/textures/item/shared.png"
        create_test_jar(
            addon,
            {
                "assets/addon/models/item/shared.json": json.dumps(
                    {"textures": {"layer0": "missingmod:item/shared"}}
                ).encode(),
                first: png_1x1(),
                second: png_1x1(),
            },
        )
        catalog = ModpackAssetCatalog.from_mods_directory(mods)
        try:
            with zipfile.ZipFile(addon) as zf:
                result = _resolve_icon_from_catalog(
                    addon,
                    "addon",
                    "item.addon.shared",
                    catalog,
                    current_zf=zf,
                    budget=None,
                )
        finally:
            catalog.close()

        assert result is None


# ==================================================
# to_halfwidth 純函式測試
# ==================================================


class TestToHalfwidth:
    """to_halfwidth 函式的各種情境測試。"""

    def test_全形數字轉半形(self):
        """全形數字轉半形"""
        from app.views.icon_preview.icon_cache import to_halfwidth

        assert to_halfwidth("１２３") == "123"

    def test_全形字母轉半形(self):
        """全形字母轉半形"""
        from app.views.icon_preview.icon_cache import to_halfwidth

        assert to_halfwidth("ＡＢＣ") == "ABC"

    def test_混合內容(self):
        """混合內容保留不需要轉換的部分"""
        from app.views.icon_preview.icon_cache import to_halfwidth

        assert to_halfwidth("Atomic Reshaper １２３") == "Atomic Reshaper 123"

    def test_空字串(self):
        """空字串不報錯"""
        from app.views.icon_preview.icon_cache import to_halfwidth

        assert to_halfwidth("") == ""

    def test_已全是半形(self):
        """已是半形的內容不變"""
        from app.views.icon_preview.icon_cache import to_halfwidth

        assert to_halfwidth("hello 123") == "hello 123"


# ==================================================
# L2 entries 快取覆寫測試
# ==================================================


class TestAtomicWrite:
    """L2 快取整個目錄換上：目標已存在時覆蓋且不留下暫存目錄。"""

    def test_overwrite_existing_cache(self, tmp_path, monkeypatch):
        from app.views.icon_preview import entries_cache

        mods = tmp_path / "mods"
        mods.mkdir()
        (mods / "a.jar").write_bytes(b"x")
        cache_root = tmp_path / "cache"
        monkeypatch.setattr(entries_cache, "_get_cache_dir", lambda: cache_root)

        entries_cache._save_entries_cache_l2(
            mods, [{"modid": "m", "key": "k1", "en": "One"}]
        )
        entries_cache._save_entries_cache_l2(
            mods, [{"modid": "m", "key": "k2", "en": "Two"}]
        )

        source = entries_cache.open_entries_cache_l2(mods)
        assert [row["key"] for row in source.load("m")] == ["k2"]
        assert [p.name for p in cache_root.iterdir()] == [
            entries_cache._compute_cache_key(mods)
        ]
