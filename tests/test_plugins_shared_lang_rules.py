from pathlib import Path

from translation_tool.plugins.shared import (
    compute_output_path,
    is_already_zh,
    is_lang_code_segment,
    replace_lang_folder_with_zh_tw,
    should_rename_to_zh_tw,
)


def test_replace_lang_folder_with_zh_tw_rewrites_all_lang_segments() -> None:
    rel = Path('quests/lang/en_us/foo/ja_jp/bar.json')
    assert replace_lang_folder_with_zh_tw(rel) == Path('quests/lang/zh_tw/foo/zh_tw/bar.json')


def test_should_rename_to_zh_tw_only_for_language_filename() -> None:
    assert should_rename_to_zh_tw(Path('en_us.json'), {'en_us'}) is True
    assert should_rename_to_zh_tw(Path('custom_map.json'), {'en_us'}) is False


def test_compute_output_path_from_public_shared_api() -> None:
    in_dir = Path('/project/in')
    out_dir = Path('/project/out')
    src = in_dir / 'lang' / 'ru_ru' / 'ru_ru.json'
    assert compute_output_path(src, in_dir, out_dir, {'ru_ru'}) == out_dir / 'lang' / 'zh_tw' / 'zh_tw.json'


def test_is_lang_code_segment_public_api_sample() -> None:
    assert is_lang_code_segment('zh_cn') is True
    assert is_lang_code_segment('zh-cn') is False


def test_is_already_zh_public_api_sample() -> None:
    assert is_already_zh('§a獲得 鐵錠') is True
    assert is_already_zh('Iron Ingot') is False
