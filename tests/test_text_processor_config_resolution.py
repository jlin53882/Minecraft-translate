import json

from translation_tool.utils import text_processor


def test_load_replace_rules_uses_explicit_runtime_path_helper(tmp_path, monkeypatch):
    rules_path = tmp_path / 'replace_rules.json'
    rules_path.write_text(
        json.dumps([
            {'from': 'abcdef', 'to': 'A'},
            {'from': 'abc', 'to': 'B'},
        ], ensure_ascii=False),
        encoding='utf-8',
    )

    monkeypatch.setattr(text_processor, '_resolve_rules_path', lambda path: rules_path)

    rules = text_processor.load_replace_rules('replace_rules.json')
    assert [r['from'] for r in rules] == ['abcdef', 'abc']
