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

    def test_bundle_with_missing_config(self, tmp_path, monkeypatch):
        """測試缺少設定時的錯誤處理"""
        from translation_tool.core.output_bundler import bundle_outputs_generator
        
        # Mock load_config 傳回空設定
        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {}  # 沒有 source_folders
                return default
        
        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())
        
        output_zip = tmp_path / "output.zip"
        results = list(bundle_outputs_generator(str(tmp_path), str(output_zip)))
        
        # 應該收到錯誤訊息
        assert len(results) > 0
        assert results[-1].get("error") is True
        assert "錯誤" in results[-1].get("log", "")

    def test_bundle_with_nonexistent_source_folder(self, tmp_path, monkeypatch):
        """測試來源資料夾不存在時的處理"""
        from translation_tool.core.output_bundler import bundle_outputs_generator
        
        # Mock load_config 傳回設定但來源資料夾不存在
        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"assets": "zh_tw_generated"}}
                return default
        
        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())
        
        output_zip = tmp_path / "output.zip"
        results = list(bundle_outputs_generator(str(tmp_path), str(output_zip)))
        
        # 應該有警告但不是錯誤
        assert len(results) > 0

    def test_bundle_success_with_valid_folders(self, tmp_path, monkeypatch):
        """測試正常打包場景"""
        from translation_tool.core.output_bundler import bundle_outputs_generator
        
        # 建立有效的來源資料夾結構
        source_folder = tmp_path / "zh_tw_generated" / "assets" / "modid" / "lang"
        source_folder.mkdir(parents=True)
        (source_folder / "zh_tw.json").write_text('{"key": "測試"}')
        
        # Mock load_config
        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"assets": "zh_tw_generated"}}
                return default
        
        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())
        
        output_zip = tmp_path / "output.zip"
        results = list(bundle_outputs_generator(str(tmp_path), str(output_zip)))
        
        # 驗證結果
        assert len(results) > 0
        assert results[-1].get("progress") == 1.0
        assert "完成" in results[-1].get("log", "")
        
        # 驗證 ZIP 檔案存在且包含正確內容
        assert output_zip.exists()
        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            assert any("zh_tw.json" in n for n in names)

    def test_bundle_with_root_folder(self, tmp_path, monkeypatch):
        """測試 root 資料夾映射到 ZIP 根目錄"""
        from translation_tool.core.output_bundler import bundle_outputs_generator
        
        # 建立 pack.mcmeta 檔案
        source_folder = tmp_path / "pack_mcmeta"
        source_folder.mkdir()
        (source_folder / "pack.mcmeta").write_text('{"pack": {}}')
        
        # Mock load_config，使用 root
        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"root": "pack_mcmeta"}}
                return default
        
        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())
        
        output_zip = tmp_path / "output.zip"
        results = list(bundle_outputs_generator(str(tmp_path), str(output_zip)))
        
        # 驗證檔案在 ZIP 根目錄
        assert output_zip.exists()
        with zipfile.ZipFile(output_zip, "r") as zf:
            assert "pack.mcmeta" in zf.namelist()


class TestWritePackMcmeta:
    """測試 _write_pack_mcmeta 函式"""

    def test_write_pack_mcmeta_with_description_and_format(self, tmp_path):
        """測試 pack.mcmeta 包含 description 和 pack_format"""
        from translation_tool.core.output_bundler import _write_pack_mcmeta

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            _write_pack_mcmeta(zf, "Test Description", 15, 15)

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
            _write_pack_mcmeta(zf, "Range Description", 9, 15)

        with zipfile.ZipFile(zip_path, "r") as zf:
            content = json.loads(zf.read("pack.mcmeta").decode("utf-8"))
            assert content["pack"]["description"] == "Range Description"
            assert content["pack"]["pack_format"] == 9
            assert content["pack"]["supported_formats"] == [9, 15]


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

    def test_bundle_with_description_and_format(self, tmp_path, monkeypatch):
        """測試 description 和 format 寫入 pack.mcmeta"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        source_folder = tmp_path / "zh_tw_generated" / "assets"
        source_folder.mkdir(parents=True)
        (source_folder / "test.json").write_text('{"key": "value"}')

        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"assets": "zh_tw_generated"}}
                return default

        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())

        output_zip = tmp_path / "output.zip"
        results = list(bundle_outputs_generator(
            str(tmp_path), str(output_zip),
            description="My Translation Pack",
            min_format=15,
            max_format=15,
        ))

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

        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())

        output_zip = tmp_path / "output.zip"
        results = list(bundle_outputs_generator(
            str(tmp_path), str(output_zip),
            pack_image_path=str(pack_image),
        ))

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
        extra.mkdir()
        (extra / "extra.json").write_text('{"extra": true}')

        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"assets": "zh_tw_generated"}}
                return default

        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())

        output_zip = tmp_path / "output.zip"
        results = list(bundle_outputs_generator(
            str(tmp_path), str(output_zip),
            extra_folders=[str(extra)],
        ))

        assert output_zip.exists()
        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            assert any("zh_tw.json" in n for n in names)
            assert "extra.json" in names

    def test_bundle_extra_folders_duplicate_handling(self, tmp_path, monkeypatch):
        """測試 extra_folders 與主資料夾有重複檔名時的處理"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        zh_tw = tmp_path / "zh_tw_generated" / "assets"
        zh_tw.mkdir(parents=True)
        (zh_tw / "shared.txt").write_text("from main")

        extra = tmp_path / "extra_folder"
        extra.mkdir()
        (extra / "subdir").mkdir()
        (extra / "subdir" / "shared.txt").write_text("from extra subdir")

        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"assets": "zh_tw_generated"}}
                return default

        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())

        output_zip = tmp_path / "output.zip"
        results = list(bundle_outputs_generator(
            str(tmp_path), str(output_zip),
            extra_folders=[str(extra)],
        ))

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

        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())

        output_zip = tmp_path / "output.zip"
        list(bundle_outputs_generator(
            str(tmp_path), str(output_zip),
            pack_image_path=str(pack_image),
        ))

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

        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())

        output_zip = tmp_path / "output.zip"
        results = list(bundle_outputs_generator(
            str(tmp_path), str(output_zip),
            pack_image_path=str(tmp_path / "nonexistent.png"),
        ))

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

        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())

        output_zip = tmp_path / "output.zip"
        results = list(bundle_outputs_generator(
            str(tmp_path), str(output_zip),
            extra_folders=[str(tmp_path / "nonexistent")],
        ))

        assert any("額外資料夾不存在" in r.get("log", "") for r in results)


class TestOutputBundlerIntegration:
    """整合測試：OutputBundler 完整流程"""

    def test_full_bundle_workflow(self, tmp_path, monkeypatch):
        """測試完整打包工作流程"""
        from translation_tool.core.output_bundler import bundle_outputs_generator
        
        # 建立多個來源資料夾
        zh_tw_folder = tmp_path / "zh_tw_generated" / "assets" / "mod1" / "lang"
        zh_tw_folder.mkdir(parents=True)
        (zh_tw_folder / "zh_tw.json").write_text('{"mod1": "內容1"}')
        
        pack_folder = tmp_path / "pack_mcmeta"
        pack_folder.mkdir()
        (pack_folder / "pack.mcmeta").write_text('{"pack": "info"}')
        
        # Mock load_config
        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {
                        "source_folders": {
                            "assets": "zh_tw_generated",
                            "root": "pack_mcmeta"
                        }
                    }
                return default
        
        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())
        
        output_zip = tmp_path / "bundle.zip"
        results = list(bundle_outputs_generator(str(tmp_path), str(output_zip)))
        
        # 驗證結果
        assert output_zip.exists()
        
        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            assert any("zh_tw.json" in n for n in names)
            assert "pack.mcmeta" in names
        
        # 驗證 progress 值
        progresses = [r.get("progress", 0) for r in results]
        assert progresses[-1] == 1.0

    def test_full_bundle_with_all_new_features(self, tmp_path, monkeypatch):
        """測試完整流程包含所有新功能"""
        from translation_tool.core.output_bundler import bundle_outputs_generator

        zh_tw = tmp_path / "zh_tw_generated" / "assets" / "mod1" / "lang"
        zh_tw.mkdir(parents=True)
        (zh_tw / "zh_tw.json").write_text('{"mod1": "中文"}')

        extra = tmp_path / "extras"
        extra.mkdir()
        (extra / "extra.json").write_text('{"extra": true}')

        pack_img = tmp_path / "pack.png"
        pack_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

        class MockConfig:
            def get(self, key, default=None):
                if key == "output_bundler":
                    return {"source_folders": {"assets": "zh_tw_generated"}}
                return default

        monkeypatch.setattr("translation_tool.core.output_bundler.load_config", lambda: MockConfig())

        output_zip = tmp_path / "output.zip"
        results = list(bundle_outputs_generator(
            str(tmp_path), str(output_zip),
            description="Full Test Pack",
            min_format=9,
            max_format=15,
            pack_image_path=str(pack_img),
            extra_folders=[str(extra)],
        ))

        assert output_zip.exists()

        with zipfile.ZipFile(output_zip, "r") as zf:
            names = zf.namelist()
            content = json.loads(zf.read("pack.mcmeta").decode("utf-8"))

            assert any("zh_tw.json" in n for n in names)
            assert "pack.png" in names
            assert "extra.json" in names
            assert content["pack"]["description"] == "Full Test Pack"
            assert content["pack"]["pack_format"] == 9
            assert content["pack"]["supported_formats"] == [9, 15]

        assert results[-1].get("progress") == 1.0
