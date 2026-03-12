from pathlib import Path

from translation_tool.core import jar_processor


def test_find_jar_files_recursively_finds_only_jar_files(tmp_path: Path):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'a' / 'x.jar').write_bytes(b'jar')
    (tmp_path / 'a' / 'y.zip').write_bytes(b'zip')
    (tmp_path / 'b').mkdir()
    (tmp_path / 'b' / 'z.jar').write_bytes(b'jar')

    found = sorted(Path(p).name for p in jar_processor.find_jar_files(str(tmp_path)))

    assert found == ['x.jar', 'z.jar']
