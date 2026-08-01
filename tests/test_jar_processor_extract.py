import re
import zipfile
from pathlib import Path

from translation_tool.core import jar_processor
from translation_tool.core.jar_processor_extract import (
    extract_from_jar_impl,
    get_file_hash,
)


def test_get_file_hash_returns_sha256_hex():
    """get_file_hash 必須回傳 SHA-256 16 進位字串（64 字元）"""
    data = b"hello world"
    hash_str = get_file_hash(data)

    # SHA-256("hello world") = b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9
    assert isinstance(hash_str, str)
    assert len(hash_str) == 64
    assert hash_str == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_get_file_hash_empty_data_returns_known_sha256():
    """空資料的 SHA-256 是已知的常數（e3b0c44...）"""
    hash_str = get_file_hash(b"")

    # SHA-256("") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
    assert hash_str == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_extract_from_jar_impl_direct_includes_jar_process_extract_impl(tmp_path: Path):
    """extract_from_jar_impl 是 jar_processor_extract.py 的公開 API（不是 wrapper）

    既有 test_jar_processor_extract.py 測的是 _extract_from_jar (wrapper),
    但 extract_from_jar_impl 是實際實作,直接測一下避免 wrapper 跟 impl 偏離。

    Args:
        tmp_path: pytest fixture 提供暫存目錄
    """
    jar_path = tmp_path / "demo-1.0.0.jar"
    with zipfile.ZipFile(jar_path, "w") as zf:
        zf.writestr("assets/demo/lang/en_us.json", '{"a":"A"}')

    result = extract_from_jar_impl(
        str(jar_path),
        str(tmp_path / "out"),
        re.compile(r"(?:assets/([^/]+)/)?lang/(en_us|zh_cn|zh_tw)\.(json|lang)$", re.IGNORECASE),
    )

    assert result == {"status": "success", "extracted": 1, "skipped": 0}
    assert (tmp_path / "out" / "assets" / "demo" / "lang" / "en_us.json").exists()


def test_extract_from_jar_writes_assets_to_stable_output_path(tmp_path: Path):
    jar_path = tmp_path / 'demo-1.0.0.jar'
    with zipfile.ZipFile(jar_path, 'w') as zf:
        zf.writestr('assets/demo/lang/en_us.json', '{"a":"A"}')

    result = jar_processor._extract_from_jar(
        str(jar_path),
        str(tmp_path / 'out'),
        re.compile(r'(?:assets/([^/]+)/)?lang/(en_us|zh_cn|zh_tw)\.(json|lang)$', re.IGNORECASE),
    )

    assert result == {'status': 'success', 'extracted': 1, 'skipped': 0}
    assert (tmp_path / 'out' / 'assets' / 'demo' / 'lang' / 'en_us.json').exists()


def test_extract_from_jar_writes_non_assets_under_extracted_folder(tmp_path: Path):
    jar_path = tmp_path / 'demo-neoforge-1.0.0.jar'
    with zipfile.ZipFile(jar_path, 'w') as zf:
        zf.writestr('lang/en_us.json', '{"a":"A"}')

    result = jar_processor._extract_from_jar(
        str(jar_path),
        str(tmp_path / 'out'),
        re.compile(r'lang/(en_us|zh_cn|zh_tw)\.(json|lang)$', re.IGNORECASE),
    )

    assert result == {'status': 'success', 'extracted': 1, 'skipped': 0}
    assert (tmp_path / 'out' / 'demo_extracted' / 'lang' / 'en_us.json').exists()
