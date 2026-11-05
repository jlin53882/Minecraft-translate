"""批次規劃的來源語系邊界：一般目錄翻譯與 Mod DB 路線共用同一組規則。

``batch_source_locale`` 遇到同批混有多種來源語系會直接拒絕，所以規劃階段必須先把不同語系
（含 ``en_us``、``zh_cn`` 這類預設語系與沒有語系的項目）分開。
"""

from __future__ import annotations

import itertools

import pytest

from translation_tool.core import lm_translator
from translation_tool.core.lm_batch_planner import (
    batch_group_key,
    count_planned_batches,
    group_items_by_batch_key,
    next_batch,
)
from translation_tool.core.source_locale import SourceLocaleError, batch_source_locale


def _item(locale, index, cache_type="lang", **extra):
    item = {
        "path": f"k{index}",
        "text": f"Text {index}",
        "cache_type": cache_type,
        **extra,
    }
    if locale is not None:
        item["source_locale"] = locale
    return item


def _batches(items):
    """與實際翻譯迴圈相同：反覆呼叫 ``next_batch`` 取得每一批。"""
    batches, start = [], 0
    while start < len(items):
        batch, _ = next_batch(
            items,
            start=start,
            lm_cfg=None,
            batch_size_for_type=lambda _cache_type: 50,
        )
        assert batch
        batches.append(batch)
        start += len(batch)
    return batches


MIXED = ["en_us", "pt_br", "zh_cn", None, "en_us", "pt_br", "zh_cn", None, "fr_fr"]


def _mixed_items():
    return [_item(locale, i) for i, locale in enumerate(MIXED)]


def test_directory_items_never_mix_source_locales_in_a_batch():
    items = group_items_by_batch_key(_mixed_items())

    for batch in _batches(items):
        # 不會拋出 SourceLocaleError，且同批最多一種明確語系
        batch_source_locale(batch)
        assert len({item.get("source_locale") for item in batch}) == 1


def test_mixing_default_and_non_default_locales_is_what_batch_source_locale_rejects():
    """前提確認：沒有隔離時，同批混語系確實會被拒絕（這是這個修正要避免的失敗）。"""
    with pytest.raises(SourceLocaleError):
        batch_source_locale([_item("en_us", 0), _item("pt_br", 1)])
    with pytest.raises(SourceLocaleError):
        batch_source_locale([_item("en_us", 0), _item("zh_cn", 1)])


def test_next_batch_also_stops_at_a_locale_change_without_pre_grouping():
    for batch in _batches(_mixed_items()):
        batch_source_locale(batch)


def test_preview_batch_count_matches_the_live_batches():
    items = group_items_by_batch_key(_mixed_items())

    planned = count_planned_batches(
        items, lm_cfg=None, batch_size_for_type=lambda _cache_type: 50
    )

    assert planned == len(_batches(items))
    # 五種語系（含沒有語系）各自一批
    assert planned == len(set(MIXED))


def test_grouping_keeps_every_item_once_and_the_order_inside_a_group():
    items = _mixed_items()
    grouped = group_items_by_batch_key(items)

    assert sorted(i["path"] for i in grouped) == sorted(i["path"] for i in items)
    pt = [i["path"] for i in grouped if i.get("source_locale") == "pt_br"]
    assert pt == ["k1", "k5"]


def test_locale_is_normalised_so_spelling_variants_share_a_batch():
    items = [_item("pt-BR", 0), _item("pt_br", 1), _item("PT_br", 2)]

    assert len({batch_group_key(item) for item in items}) == 1
    assert len(_batches(group_items_by_batch_key(items))) == 1


def test_invalid_locale_is_isolated_and_only_its_own_batch_fails_closed():
    items = group_items_by_batch_key(
        [_item("en_us", 0), _item("!!bad!!", 1), _item("en_us", 2)]
    )
    batches = _batches(items)

    assert len(batches) == 2
    failures = 0
    for batch in batches:
        try:
            batch_source_locale(batch)
        except SourceLocaleError:
            failures += 1
    assert failures == 1


def test_cache_types_still_split_batches():
    items = [_item("en_us", 0, "lang"), _item("en_us", 1, "patchouli")]

    assert len(_batches(group_items_by_batch_key(items))) == 2


def test_mod_db_groups_keep_their_locale_boundary():
    pt = _item("pt_br", 0, _batch_group=("foo", "lang", "pt_br"))
    en = _item("en_us", 1, _batch_group=("foo", "lang"))
    unknown = _item(None, 2, _batch_group=("foo", "lang"))

    assert batch_group_key(pt) == ("foo", "lang", "pt_br")
    assert batch_group_key(en) == ("foo", "lang", "en_us")
    assert batch_group_key(unknown) == ("foo", "lang", "")


@pytest.mark.parametrize("perm", list(itertools.permutations(range(4))))
def test_result_does_not_depend_on_the_input_order(perm):
    base = [_item("en_us", 0), _item("pt_br", 1), _item("zh_cn", 2), _item(None, 3)]
    items = group_items_by_batch_key([base[i] for i in perm])

    for batch in _batches(items):
        batch_source_locale(batch)
    assert len(_batches(items)) == 4


def test_directory_split_returns_items_grouped_for_batching(monkeypatch):
    """目錄翻譯的待翻清單（預覽、實際批次與續跑清單共用）已依語系分組。"""
    items = _mixed_items()
    monkeypatch.setattr(
        lm_translator, "_split_directory_items", lambda all_items: ([], list(all_items))
    )
    monkeypatch.setattr(
        lm_translator, "split_db_hits", lambda _ctx, all_items: ([], list(all_items))
    )

    _cached, to_translate = lm_translator._split_db_cache(items, None, True)

    for batch in _batches(to_translate):
        batch_source_locale(batch)
    assert to_translate == group_items_by_batch_key(items)
