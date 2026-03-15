"""lang_merge_pipeline.py 單元測試。

用途：測試語言合併流水線的核心函式。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from translation_tool.core.lang_merge_pipeline import _process_single_mod


def _create_test_zip(tmp_path: Path, files: dict) -> Path:
    """建立測試用 ZIP 檔案。"""
    zip_path = tmp_path / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for path, content in files.items():
            if isinstance(content, bytes):
                zf.writestr(path, content)
            else:
                zf.writestr(path, content)
    return zip_path


class TestProcessSingleMod:
    """測試 _process_single_mod 函式。"""

    def test_process_mod_with_zh_cn_only(self, tmp_path: Path) -> None:
        """測試只有 zh_cn 的情況。"""
        output_dir = tmp_path / "output"
        must_translate_dir = tmp_path / "pending"
        output_dir.mkdir()
        must_translate_dir.mkdir()

        zip_path = _create_test_zip(tmp_path, {
            "assets/demo/lang/zh_cn.json": json.dumps({
                "item.demo": "簡體內容"
            }),
        })

        paths = {
            "zh_cn": "assets/demo/lang/zh_cn.json",
        }

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _process_single_mod(
                zf=zf,
                paths=paths,
                rules=[],  # 空規則
                output_dir=str(output_dir),
                must_translate_dir=str(must_translate_dir),
            )

        assert result["success"] is True
        assert result["pending_count"] >= 0

    def test_process_mod_with_zh_cn_and_en_us(self, tmp_path: Path) -> None:
        """測試有 zh_cn 和 en_us 的情況。"""
        output_dir = tmp_path / "output"
        must_translate_dir = tmp_path / "pending"
        output_dir.mkdir()
        must_translate_dir.mkdir()

        zip_path = _create_test_zip(tmp_path, {
            "assets/demo/lang/zh_cn.json": json.dumps({
                "item.demo": "簡體內容"
            }),
            "assets/demo/lang/en_us.json": json.dumps({
                "item.demo": "English Content"
            }),
        })

        paths = {
            "zh_cn": "assets/demo/lang/zh_cn.json",
            "en_us": "assets/demo/lang/en_us.json",
        }

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _process_single_mod(
                zf=zf,
                paths=paths,
                rules=[],
                output_dir=str(output_dir),
                must_translate_dir=str(must_translate_dir),
            )

        assert result["success"] is True
        # 英文內容應該被放入 pending
        zh_tw_path = output_dir / "assets" / "demo" / "lang" / "zh_tw.json"
        assert zh_tw_path.exists()

    def test_process_mod_protects_existing_tw_translation(self, tmp_path: Path) -> None:
        """測試保護已存在的繁體翻譯。"""
        output_dir = tmp_path / "output"
        must_translate_dir = tmp_path / "pending"
        output_dir.mkdir()
        must_translate_dir.mkdir()

        # 先建立已存在的 zh_tw.json
        existing_tw = output_dir / "assets" / "demo" / "lang"
        existing_tw.mkdir(parents=True)
        existing_tw_file = existing_tw / "zh_tw.json"
        existing_tw_file.write_text(json.dumps({
            "item.demo": "現有翻譯"
        }), encoding="utf-8")

        zip_path = _create_test_zip(tmp_path, {
            "assets/demo/lang/zh_cn.json": json.dumps({
                "item.demo": "新簡體內容"
            }),
            "assets/demo/lang/en_us.json": json.dumps({
                "item.demo": "New English"
            }),
        })

        paths = {
            "zh_cn": "assets/demo/lang/zh_cn.json",
            "en_us": "assets/demo/lang/en_us.json",
        }

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _process_single_mod(
                zf=zf,
                paths=paths,
                rules=[],
                output_dir=str(output_dir),
                must_translate_dir=str(must_translate_dir),
            )

        assert result["success"] is True
        # 現有翻譯不應被覆蓋
        zh_tw_content = json.loads(existing_tw_file.read_text(encoding="utf-8"))
        assert zh_tw_content["item.demo"] == "現有翻譯"

    def test_process_mod_with_lang_format(self, tmp_path: Path) -> None:
        """測試處理 .lang 格式檔案。"""
        output_dir = tmp_path / "output"
        must_translate_dir = tmp_path / "pending"
        output_dir.mkdir()
        must_translate_dir.mkdir()

        # 測試同時有 zh_cn 和 en_us 的 lang 格式
        zip_path = _create_test_zip(tmp_path, {
            "assets/demo/lang/zh_cn.lang": "item.demo=簡體\nitem.new=新項目",
            "assets/demo/lang/en_us.lang": "item.demo=English\nitem.new=New Item",
        })

        paths = {
            "zh_cn": "assets/demo/lang/zh_cn.lang",
            "en_us": "assets/demo/lang/en_us.lang",
        }

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _process_single_mod(
                zf=zf,
                paths=paths,
                rules=[],
                output_dir=str(output_dir),
                must_translate_dir=str(must_translate_dir),
            )

        assert result["success"] is True
        # 應該輸出為 .lang 格式
        zh_tw_path = output_dir / "assets" / "demo" / "lang" / "zh_tw.lang"
        assert zh_tw_path.exists()
        # 驗證內容格式正確 (key=value 格式)
        content = zh_tw_path.read_text(encoding="utf-8")
        assert "item.demo" in content or "item.new" in content

    def test_process_mod_handles_empty_keys(self, tmp_path: Path) -> None:
        """測試處理空鍵值的情況。"""
        output_dir = tmp_path / "output"
        must_translate_dir = tmp_path / "pending"
        output_dir.mkdir()
        must_translate_dir.mkdir()

        zip_path = _create_test_zip(tmp_path, {
            "assets/demo/lang/en_us.json": json.dumps({
                "item.demo": "English",
                "empty.item": "",
                "null.item": None,
            }),
        })

        paths = {
            "en_us": "assets/demo/lang/en_us.json",
        }

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _process_single_mod(
                zf=zf,
                paths=paths,
                rules=[],
                output_dir=str(output_dir),
                must_translate_dir=str(must_translate_dir),
            )

        assert result["success"] is True

    def test_process_mod_with_mixed_cjk_and_english(self, tmp_path: Path) -> None:
        """測試混合包含 CJK 和英文的內容。"""
        output_dir = tmp_path / "output"
        must_translate_dir = tmp_path / "pending"
        output_dir.mkdir()
        must_translate_dir.mkdir()

        zip_path = _create_test_zip(tmp_path, {
            "assets/demo/lang/zh_cn.json": json.dumps({
                "item.cjk": "這是中文",
                "item.eng": "This is English",
                "item.mixed": "Hello 你好",
            }),
            "assets/demo/lang/en_us.json": json.dumps({
                "item.cjk": "Chinese",
                "item.eng": "English",
                "item.mixed": "Mixed",
            }),
        })

        paths = {
            "zh_cn": "assets/demo/lang/zh_cn.json",
            "en_us": "assets/demo/lang/en_us.json",
        }

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _process_single_mod(
                zf=zf,
                paths=paths,
                rules=[],  # 空規則
                output_dir=str(output_dir),
                must_translate_dir=str(must_translate_dir),
            )

        assert result["success"] is True
        zh_tw_path = output_dir / "assets" / "demo" / "lang" / "zh_tw.json"
        assert zh_tw_path.exists()

    def test_process_mod_with_zh_tw_source(self, tmp_path: Path) -> None:
        """測試有 zh_tw 來源的情況。"""
        output_dir = tmp_path / "output"
        must_translate_dir = tmp_path / "pending"
        output_dir.mkdir()
        must_translate_dir.mkdir()

        zip_path = _create_test_zip(tmp_path, {
            "assets/demo/lang/zh_tw.json": json.dumps({
                "item.demo": "現有翻譯"
            }),
            "assets/demo/lang/en_us.json": json.dumps({
                "item.demo": "English"
            }),
        })

        paths = {
            "zh_tw": "assets/demo/lang/zh_tw.json",
            "en_us": "assets/demo/lang/en_us.json",
        }

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _process_single_mod(
                zf=zf,
                paths=paths,
                rules=[],
                output_dir=str(output_dir),
                must_translate_dir=str(must_translate_dir),
            )

        assert result["success"] is True

    def test_process_mod_with_nested_path(self, tmp_path: Path) -> None:
        """測試嵌套路徑的處理。"""
        output_dir = tmp_path / "output"
        must_translate_dir = tmp_path / "pending"
        output_dir.mkdir()
        must_translate_dir.mkdir()

        zip_path = _create_test_zip(tmp_path, {
            "assets/mods/somemod/lang/en_us.json": json.dumps({
                "item.demo": "English"
            }),
        })

        paths = {
            "en_us": "assets/mods/somemod/lang/en_us.json",
        }

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _process_single_mod(
                zf=zf,
                paths=paths,
                rules=[],
                output_dir=str(output_dir),
                must_translate_dir=str(must_translate_dir),
            )

        assert result["success"] is True
