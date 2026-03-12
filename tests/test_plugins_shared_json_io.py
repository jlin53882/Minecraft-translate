import json
from pathlib import Path

import pytest

from translation_tool.plugins.shared import collect_json_files, read_json_dict, write_json_dict


def test_collect_json_files_returns_sorted_recursive_list(tmp_path: Path) -> None:
    (tmp_path / 'b').mkdir()
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b' / '2.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'a' / '1.json').write_text('{}', encoding='utf-8')

    files = collect_json_files(tmp_path)
    assert [p.name for p in files] == ['1.json', '2.json']


def test_read_write_json_dict_via_public_api_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / 'nested' / 'sample.json'
    payload = {'k': '測試'}
    write_json_dict(target, payload)
    assert read_json_dict(target) == payload


def test_read_json_dict_rejects_non_dict_root(tmp_path: Path) -> None:
    target = tmp_path / 'list.json'
    target.write_text(json.dumps([1, 2]), encoding='utf-8')
    with pytest.raises(ValueError):
        read_json_dict(target)
