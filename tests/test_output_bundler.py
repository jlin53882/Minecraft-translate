"""test_output_bundler.py - 輸出打包模組測試單元。

用途：測試 output_bundler.py 的功能。
"""

import json
import zipfile


class TestAddFolderToZip:
    """測試 _add_folder_to_zip 函式"""

    def test_add_folder_to_zip_empty_folder(self, tmp_path):
        """測試空資料夾的處理"""
        from translation_tool.core.output_bundler import _add_folder_to_zip

        # 建立空資料夾
        empty_folder = tmp_path / "empty"
        empty_folder.mkdir()

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            count, _ = _add_folder_to_zip(zf, str(empty_folder), "assets")

        assert count == 0

    def test_add_folder_to_zip_nonexistent_folder(self, tmp_path):
        """測試不存在的資料夾處理"""
        from translation_tool.core.output_bundler import _add_folder_to_zip

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            count, _ = _add_folder_to_zip(zf, str(tmp_path / "nonexistent"), "assets")

        assert count == 0

    def test_add_folder_to_zip_with_files(self, tmp_path):
        """測試包含檔案的資料夾處理"""
        from translation_tool.core.output_bundler import _add_folder_to_zip

        # 建立測試資料夾結構
        source_folder = tmp_path / "source" / "assets" / "modid" / "lang"
        source_folder.mkdir(parents=True)

        # 建立測試檔案
        (source_folder / "en_us.json").write_text('{"key": "value"}')
        (source_folder / "zh_tw.json").write_text('{"key": "翻譯"}')

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            count, _ = _add_folder_to_zip(zf, str(source_folder), "assets")

        assert count == 2

        # 驗證 ZIP 內容
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert any("en_us.json" in n for n in names)
            assert any("zh_tw.json" in n for n in names)


class TestBundleOutputsGenerator:
    """測試 bundle_outputs_generator 生成器函式"""

    def test_bundle_with_nonexistent_input_folder(self, tmp_path):
        """測試來源資料夾不存在時的處理"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        output_zip = tmp_path / "output.zip"
        results = list(
            bundle_outputs_generator(str(tmp_path / "nonexistent"), str(output_zip))
        )

        assert len(results) > 0
        assert results[-1].get("error") is True
        assert "不存在" in results[-1].get("log", "")

    def test_bundle_adds_all_files_from_root(self, tmp_path):
        """只收集 assets/，不把散落根目錄檔案當成資源包內容。"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        lang = tmp_path / "assets" / "demo" / "lang"
        lang.mkdir(parents=True)
        (lang / "zh_tw.json").write_text('{"test": "value"}')
        (tmp_path / "translation_map.json").write_text("{}")

        output_zip = tmp_path / "output.zip"
        list(bundle_outputs_generator(str(tmp_path), str(output_zip)))

        assert output_zip.exists()
        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            assert "assets/demo/lang/zh_tw.json" in names
            assert "translation_map.json" not in names

    def test_bundle_pack_mcmeta_from_folder_overrides_ui(self, tmp_path):
        """測試 input_root_dir 中有 pack.mcmeta 時優先使用，忽略 UI 設定"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        lang = tmp_path / "assets" / "demo" / "lang"
        lang.mkdir(parents=True)
        (lang / "zh_tw.json").write_text('{"test": "value"}')

        (tmp_path / "pack.mcmeta").write_text(
            '{"pack":{"description":"FolderVersion","pack_format":15}}'
        )

        output_zip = tmp_path / "output.zip"
        results = list(
            bundle_outputs_generator(
                str(tmp_path),
                str(output_zip),
                description="UIVersion",
                pack_format=34,
            )
        )

        assert output_zip.exists()
        with zipfile.ZipFile(output_zip, "r") as zf:
            content = json.loads(zf.read("pack.mcmeta").decode("utf-8"))
            assert content["pack"]["description"] == "FolderVersion"
            assert content["pack"]["pack_format"] == 15

        warning_logs = [r.get("log", "") for r in results if "警告" in r.get("log", "")]
        assert any("pack.mcmeta" in log for log in warning_logs)

    def test_bundle_pack_png_from_folder_overrides_ui(self, tmp_path):
        """測試 input_root_dir 中有 pack.png 時優先使用，忽略 UI 設定"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        lang = tmp_path / "assets" / "demo" / "lang"
        lang.mkdir(parents=True)
        (lang / "zh_tw.json").write_text('{"test": "value"}')

        (tmp_path / "pack.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

        ui_pack_png = tmp_path / "ui_pack.png"
        ui_pack_png.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)

        output_zip = tmp_path / "output.zip"
        results = list(
            bundle_outputs_generator(
                str(tmp_path),
                str(output_zip),
                pack_image_path=str(ui_pack_png),
            )
        )

        assert output_zip.exists()
        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            assert "pack.png" in names

        warning_logs = [r.get("log", "") for r in results if "警告" in r.get("log", "")]
        assert any("pack.png" in log for log in warning_logs)

        assert output_zip.exists()
        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            assert "assets/demo/lang/zh_tw.json" in names


class TestWritePackMcmeta:
    """測試 _write_pack_mcmeta 函式"""

    def test_write_pack_mcmeta_with_description_and_format(self, tmp_path):
        """測試 pack.mcmeta 包含 description 和 pack_format"""
        from translation_tool.core.output_bundler import _write_pack_mcmeta

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            _write_pack_mcmeta(zf, "Test Description", 15)

        with zipfile.ZipFile(zip_path, "r") as zf:
            content = json.loads(zf.read("pack.mcmeta").decode("utf-8"))
            assert content["pack"]["description"] == "Test Description"
            assert content["pack"]["pack_format"] == 15
            assert "supported_formats" not in content["pack"]

    def test_write_pack_mcmeta_with_supported_formats(self, tmp_path):
        """測試 pack.mcmeta 包含 supported_formats 範圍"""
        from translation_tool.core.output_bundler import _write_pack_mcmeta

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            _write_pack_mcmeta(zf, "Range Description", 34)

        with zipfile.ZipFile(zip_path, "r") as zf:
            content = json.loads(zf.read("pack.mcmeta").decode("utf-8"))
            assert content["pack"]["description"] == "Range Description"
            assert content["pack"]["pack_format"] == 34
            assert content["pack"]["supported_formats"] == {
                "min_inclusive": 34,
                "max_inclusive": 46,
            }


class TestAddFolderToZipDuplicateHandling:
    """測試 _add_folder_to_zip 重複檔名處理"""

    def test_add_folder_to_zip_duplicates_rename(self, tmp_path):
        """測試相同檔名會自動加上 _1, _2 後綴"""
        from translation_tool.core.output_bundler import _add_folder_to_zip

        folder1 = tmp_path / "folder1"
        folder2 = tmp_path / "folder2"
        folder1.mkdir(parents=True)
        folder2.mkdir(parents=True)
        (folder1 / "file.txt").write_text("content1")
        (folder2 / "file.txt").write_text("content2")

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            count1, seen = _add_folder_to_zip(zf, str(folder1), "root", {})
            count2, _ = _add_folder_to_zip(zf, str(folder2), "root", seen)

        assert count1 == 1
        assert count2 == 1

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "root/file.txt" in names
            assert "root/file_1.txt" in names


class TestBundleOutputsGeneratorNewParams:
    """測試 bundle_outputs_generator 新參數"""

    def test_bundle_with_description_and_format(self, tmp_path):
        """測試 description 和 format 寫入 pack.mcmeta"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        source_folder = tmp_path / "assets"
        source_folder.mkdir(parents=True)
        (source_folder / "test.json").write_text('{"key": "value"}')

        output_zip = tmp_path / "output.zip"
        list(
            bundle_outputs_generator(
                str(tmp_path),
                str(output_zip),
                description="My Translation Pack",
                pack_format=15,
            )
        )

        assert output_zip.exists()
        with zipfile.ZipFile(output_zip, "r") as zf:
            content = json.loads(zf.read("pack.mcmeta").decode("utf-8"))
            assert content["pack"]["description"] == "My Translation Pack"
            assert content["pack"]["pack_format"] == 15

    def test_bundle_with_pack_image(self, tmp_path, monkeypatch):
        """測試 pack_image_path 複製圖片到 ZIP"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        source_folder = tmp_path / "zh_tw_generated" / "assets"
        source_folder.mkdir(parents=True)
        (source_folder / "test.json").write_text('{"key": "value"}')

        pack_image = tmp_path / "pack.png"
        pack_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"assets": "zh_tw_generated"}}
                return default

        monkeypatch.setattr(
            "translation_tool.core.output_bundler.load_config", lambda: MockConfig()
        )

        output_zip = tmp_path / "output.zip"
        list(
            bundle_outputs_generator(
                str(tmp_path),
                str(output_zip),
                pack_image_path=str(pack_image),
            )
        )

        assert output_zip.exists()
        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            assert "pack.png" in names

    def test_bundle_with_extra_folders(self, tmp_path, monkeypatch):
        """測試 extra_folders 合併到 ZIP 根目錄"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        zh_tw = tmp_path / "zh_tw_generated" / "assets"
        zh_tw.mkdir(parents=True)
        (zh_tw / "zh_tw.json").write_text('{"key": "中文"}')

        extra = tmp_path / "extra_folder"
        extra_lang = extra / "assets" / "extra_mod" / "lang"
        extra_lang.mkdir(parents=True)
        (extra_lang / "zh_tw.json").write_text('{"extra": true}')

        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"assets": "zh_tw_generated"}}
                return default

        monkeypatch.setattr(
            "translation_tool.core.output_bundler.load_config", lambda: MockConfig()
        )

        output_zip = tmp_path / "output.zip"
        list(
            bundle_outputs_generator(
                str(tmp_path),
                str(output_zip),
                extra_folders=[str(extra)],
            )
        )

        assert output_zip.exists()
        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            assert any("zh_tw.json" in n for n in names)
            assert "assets/extra_mod/lang/zh_tw.json" in names

    def test_bundle_extra_folders_duplicate_handling(self, tmp_path, monkeypatch):
        """測試 extra_folders 與主資料夾有重複檔名時的處理"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        zh_tw = tmp_path / "zh_tw_generated" / "assets"
        zh_tw.mkdir(parents=True)
        (zh_tw / "shared.txt").write_text("from main")

        extra = tmp_path / "extra_folder"
        extra_assets = extra / "assets" / "extra_mod"
        extra_assets.mkdir(parents=True)
        (extra_assets / "shared.txt").write_text("from extra subdir")

        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"assets": "zh_tw_generated"}}
                return default

        monkeypatch.setattr(
            "translation_tool.core.output_bundler.load_config", lambda: MockConfig()
        )

        output_zip = tmp_path / "output.zip"
        list(
            bundle_outputs_generator(
                str(tmp_path),
                str(output_zip),
                extra_folders=[str(extra)],
            )
        )

        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            assert any("shared.txt" in n for n in names)

    def test_bundle_with_jpg_pack_image(self, tmp_path, monkeypatch):
        """測試 jpg 圖片也能複製為 pack.png"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        source_folder = tmp_path / "zh_tw_generated" / "assets"
        source_folder.mkdir(parents=True)
        (source_folder / "test.json").write_text('{"key": "value"}')

        pack_image = tmp_path / "pack.jpg"
        pack_image.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)

        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"assets": "zh_tw_generated"}}
                return default

        monkeypatch.setattr(
            "translation_tool.core.output_bundler.load_config", lambda: MockConfig()
        )

        output_zip = tmp_path / "output.zip"
        list(
            bundle_outputs_generator(
                str(tmp_path),
                str(output_zip),
                pack_image_path=str(pack_image),
            )
        )

        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            assert "pack.png" in names

    def test_bundle_pack_image_nonexistent(self, tmp_path, monkeypatch):
        """測試 pack_image_path 不存在時略過"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        source_folder = tmp_path / "zh_tw_generated" / "assets"
        source_folder.mkdir(parents=True)
        (source_folder / "test.json").write_text('{"key": "value"}')

        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"assets": "zh_tw_generated"}}
                return default

        monkeypatch.setattr(
            "translation_tool.core.output_bundler.load_config", lambda: MockConfig()
        )

        output_zip = tmp_path / "output.zip"
        list(
            bundle_outputs_generator(
                str(tmp_path),
                str(output_zip),
                pack_image_path=str(tmp_path / "nonexistent.png"),
            )
        )

        assert output_zip.exists()
        with zipfile.ZipFile(output_zip, "r") as zf:
            assert "pack.png" not in zf.namelist()

    def test_bundle_extra_folder_nonexistent(self, tmp_path, monkeypatch):
        """測試 extra_folders 有不存在的路徑時略過"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        source_folder = tmp_path / "zh_tw_generated" / "assets"
        source_folder.mkdir(parents=True)
        (source_folder / "test.json").write_text('{"key": "value"}')

        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"assets": "zh_tw_generated"}}
                return default

        monkeypatch.setattr(
            "translation_tool.core.output_bundler.load_config", lambda: MockConfig()
        )

        output_zip = tmp_path / "output.zip"
        results = list(
            bundle_outputs_generator(
                str(tmp_path),
                str(output_zip),
                extra_folders=[str(tmp_path / "nonexistent")],
            )
        )

        assert any("額外項目不存在" in r.get("log", "") for r in results)


class TestOutputBundlerIntegration:
    """整合測試：OutputBundler 完整流程"""

    def test_full_bundle_workflow(self, tmp_path):
        """測試完整打包工作流程"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        zh_tw_folder = tmp_path / "assets" / "mod1" / "lang"
        zh_tw_folder.mkdir(parents=True)
        (zh_tw_folder / "zh_tw.json").write_text('{"mod1": "內容1"}')

        output_zip = tmp_path / "bundle.zip"
        results = list(bundle_outputs_generator(str(tmp_path), str(output_zip)))

        assert output_zip.exists()

        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            assert any("zh_tw.json" in n for n in names)

        progresses = [r.get("progress", 0) for r in results]
        assert progresses[-1] == 1.0

    def test_full_bundle_with_all_new_features(self, tmp_path):
        """測試完整流程包含所有新功能"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        zh_tw = tmp_path / "assets" / "mod1" / "lang"
        zh_tw.mkdir(parents=True)
        (zh_tw / "zh_tw.json").write_text('{"mod1": "中文"}')

        extra = tmp_path / "extras" / "assets"
        extra.mkdir(parents=True)
        (extra / "extra.json").write_text('{"extra": true}')

        pack_img = tmp_path / "pack.png"
        pack_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

        output_zip = tmp_path / "output.zip"
        results = list(
            bundle_outputs_generator(
                str(tmp_path),
                str(output_zip),
                description="Full Test Pack",
                pack_format=34,
                pack_image_path=str(pack_img),
                extra_folders=[str(extra)],
            )
        )

        assert output_zip.exists()

        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            content = json.loads(zf.read("pack.mcmeta").decode("utf-8"))

            assert any("zh_tw.json" in n for n in names)
            assert "pack.png" in names
            assert "assets/extra.json" in names
            assert content["pack"]["description"] == "Full Test Pack"
            assert content["pack"]["pack_format"] == 34
            assert content["pack"]["supported_formats"]["min_inclusive"] == 34

        assert results[-1].get("progress") == 1.0

    def test_resource_pack_contract_excludes_data_and_nested_pack_metadata(self, tmp_path):
        from translation_tool.core.output_bundler import bundle_outputs_generator

        lang = tmp_path / "root" / "assets" / "demo" / "lang"
        lang.mkdir(parents=True)
        (lang / "zh_tw.json").write_text('{"item.demo": "示範"}')
        (tmp_path / "root" / "pack.mcmeta").write_text(
            '{"pack": {"description": "root", "pack_format": 34}}'
        )
        (tmp_path / "assets" / "demo" / "pack.mcmeta").parent.mkdir(
            parents=True, exist_ok=True
        )
        (tmp_path / "assets" / "demo" / "pack.mcmeta").write_text("{}")
        (tmp_path / "pack_1.mcmeta").write_text("{}")
        (tmp_path / "data" / "demo" / "advancements").mkdir(parents=True)
        (tmp_path / "data" / "demo" / "advancements" / "start.json").write_text(
            "{}"
        )
        (tmp_path / "other_output" / "assets").mkdir(parents=True)
        (tmp_path / "other_output" / "assets" / "ignored.json").write_text("{}")

        output_zip = tmp_path / "resource-pack.zip"
        updates = list(bundle_outputs_generator(str(tmp_path), str(output_zip)))

        with zipfile.ZipFile(output_zip) as zf:
            names = zf.namelist()
        assert names.count("pack.mcmeta") == 1
        assert "assets/demo/lang/zh_tw.json" in names
        assert all(
            name in {"pack.mcmeta", "pack.png"} or name.startswith("assets/")
            for name in names
        )
        assert not any(name.lower().endswith("pack_1.mcmeta") for name in names)
        assert not any(name.lower().startswith("data/") for name in names)
        assert not any(name.lower().startswith("other_output/") for name in names)
        assert any("非 Resource Pack" in update.get("log", "") for update in updates)


def test_bundle_result_matches_the_log_and_marks_reuse(tmp_path):
    import re

    from app.views.bundler_view import bundle_result_text
    from translation_tool.core.output_bundler import bundle_outputs_generator

    lang = tmp_path / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "zh_tw.json").write_text('{"test": "value"}')
    output_zip = tmp_path / "output.zip"

    updates = list(bundle_outputs_generator(str(tmp_path), str(output_zip)))
    result = updates[-1]["bundle_result"]
    logged = int(re.search(r"總共 (\d+) 個檔案", updates[-1]["log"]).group(1))
    assert result["reused"] is False and result["files"] == logged
    assert bundle_result_text(result).startswith(f"打包完成：{logged:,} 個檔案加入 ZIP")

    again = list(bundle_outputs_generator(str(tmp_path), str(output_zip)))
    assert again[-1]["bundle_result"] == {"reused": True, "files": None}
    assert "沿用上次的 ZIP" in bundle_result_text(again[-1]["bundle_result"])
