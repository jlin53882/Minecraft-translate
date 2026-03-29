import re
import zipfile
from pathlib import Path

from translation_tool.core import jar_processor


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
