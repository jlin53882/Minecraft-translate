"""tests/test_zip_prefix_handling.py
用途：驗證 lang_merge_pipeline.py 的 ZIP 包裝前綴警告邏輯。
已知前綴（lang_out / book_out / patchouli_out）不應觸發警告，
未知前綴應呼叫 log_warning。
"""
import io
import zipfile
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from translation_tool.core.lang_merge_pipeline import (
    _process_single_mod,
    KNOWN_ZIP_PACKAGING_PREFIXES,
)


def _make_zip_with_prefix(prefix: str) -> zipfile.ZipFile:
    """建立包含有效 .lang 內容的 ZIP，header 為指定前綴。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        base = f"{prefix}/assets/testmod/lang/"
        # 有效 .lang 內容：英文 key=value（不含 CJK，避免觸發翻譯邏輯）
        content = "hello=Hello World\nbye=Goodbye\n"
        zf.writestr(base + "zh_cn.lang", content.encode("utf-8"))
        zf.writestr(base + "zh_tw.lang", content.encode("utf-8"))
        zf.writestr(base + "en_us.lang", content.encode("utf-8"))
    buf.seek(0)
    return zipfile.ZipFile(buf, "r")


def _paths_for(prefix: str) -> dict:
    base = f"{prefix}/assets/testmod/lang/"
    return {
        "zh_cn": base + "zh_cn.lang",
        "zh_tw": base + "zh_tw.lang",
        "en_us": base + "en_us.lang",
    }


class TestKnownZipPrefixesNoWarning:
    """已知前綴不應觸發 log_warning。"""

    @pytest.mark.parametrize("prefix", sorted(KNOWN_ZIP_PACKAGING_PREFIXES))
    def test_whitelisted_prefix_no_warning(self, prefix: str):
        """whitelist 前綴 'lang_out' / 'book_out' / 'patchouli_out' 不應呼叫 log_warning。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zf = _make_zip_with_prefix(prefix)
            paths = _paths_for(prefix)
            with patch("translation_tool.core.lang_merge_pipeline.log_warning") as mock_warn:
                _process_single_mod(
                    zf=zf,
                    paths=paths,
                    rules=[],
                    output_dir=str(tmp_path / "output"),
                    must_translate_dir=str(tmp_path / "pending"),
                )
                mock_warn.assert_not_called()
            zf.close()


class TestUnknownZipPrefixWarning:
    """未知前綴應觸發 log_warning。"""

    def test_unknown_prefix_calls_log_warning_once(self):
        """自訂前綴 'custom_out'（不在白名單）應觸發一次警告，訊息包含前綴名。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zf = _make_zip_with_prefix("custom_out")
            paths = _paths_for("custom_out")
            with patch("translation_tool.core.lang_merge_pipeline.log_warning") as mock_warn:
                _process_single_mod(
                    zf=zf,
                    paths=paths,
                    rules=[],
                    output_dir=str(tmp_path / "output"),
                    must_translate_dir=str(tmp_path / "pending"),
                )
                mock_warn.assert_called_once()
                args, _ = mock_warn.call_args
                assert "custom_out" in args[0]
            zf.close()

    def test_dot_prefix_no_warning(self):
        """以 '.' 開頭的前綴依邏輯不會警告（hidden folder）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zf = _make_zip_with_prefix(".hidden_out")
            paths = _paths_for(".hidden_out")
            with patch("translation_tool.core.lang_merge_pipeline.log_warning") as mock_warn:
                _process_single_mod(
                    zf=zf,
                    paths=paths,
                    rules=[],
                    output_dir=str(tmp_path / "output"),
                    must_translate_dir=str(tmp_path / "pending"),
                )
                mock_warn.assert_not_called()
            zf.close()
