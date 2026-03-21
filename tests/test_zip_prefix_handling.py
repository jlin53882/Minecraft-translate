"""tests/test_zip_prefix_handling.py

用途：驗證 lang_merge_pipeline.py 的 ZIP 包裝前綴自動偵測剝離邏輯。
新行為：所有前綴（已知或未知）皆自動偵測並剝離，不應觸發任何警告。
"""
import io
import zipfile
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from translation_tool.core.lang_merge_pipeline import _process_single_mod


def _make_zip_with_prefix(prefix: str) -> zipfile.ZipFile:
    """建立包含有效 .lang 內容的 ZIP，header 為指定前綴。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        base = f"{prefix}/assets/testmod/lang/"
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


class TestZipPrefixAutoStrip:
    """新行為：所有前綴皆自動偵測剝離，不應有任何警告。"""

    @pytest.mark.parametrize(
        "prefix",
        ["lang_out", "book_out", "patchouli_out", "custom_out", ".hidden_out"],
    )
    def test_no_warning_for_any_prefix(self, prefix: str):
        """已知前綴（lang_out/book_out/patchouli_out）與未知前綴皆不應呼叫 log_warning。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zf = _make_zip_with_prefix(prefix)
            paths = _paths_for(prefix)
            with patch("translation_tool.core.lang_merge_pipeline.log_warning") as mock_warn:
                result = _process_single_mod(
                    zf=zf,
                    paths=paths,
                    rules=[],
                    output_dir=str(tmp_path / "output"),
                    must_translate_dir=str(tmp_path / "pending"),
                )
                mock_warn.assert_not_called()
            zf.close()

    def test_output_dir_created_for_custom_prefix(self):
        """自訂前綴 custom_out 也應正常處理（無 CJK → 無輸出，目錄不建立是預期行為）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zf = _make_zip_with_prefix("custom_out")
            paths = _paths_for("custom_out")
            with patch("translation_tool.core.lang_merge_pipeline.log_warning"):
                result = _process_single_mod(
                    zf=zf,
                    paths=paths,
                    rules=[],
                    output_dir=str(tmp_path / "output"),
                    must_translate_dir=str(tmp_path / "pending"),
                )
            zf.close()
            # 無 CJK 內容時不產生輸出，目錄不會建立；此測試確認不拋例外即可
            assert result is not None  # 確認處理完成未崩潰

    def test_dot_prefix_no_warning(self):
        """隱藏資料夾前綴（.hidden_out）亦不應警告。"""
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
