"""translate_batch_smart 對空白原文項目的位置契約：第 i 筆結果對應 batch_items[i]。"""

from translation_tool.core import lm_translator_main as main


def _item(text, key="k"):
    return {"text": text, "key": key, "cache_type": "lang"}


def _fake_core(calls):
    def fake(batch_items, total, dry_run, *, event_callback=None):
        calls.append([item["text"] for item in batch_items])
        return [{**item, "text": f"譯:{item['text']}"} for item in batch_items], "AUTO"

    return fake


def test_all_blank_batch_returns_items_in_place_without_calling_engine(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_translate_non_blank_batch", _fake_core(calls))
    batch = [_item("", "a"), _item("   ", "b")]

    results, status = main.translate_batch_smart(batch, 2)

    assert status == "AUTO"
    assert calls == []
    assert [r["key"] for r in results] == ["a", "b"]
    assert all(r["_untranslated"] for r in results)


def test_blank_item_keeps_original_position(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_translate_non_blank_batch", _fake_core(calls))
    batch = [_item("A", "a"), _item(" ", "b"), _item("C", "c")]

    results, status = main.translate_batch_smart(batch, 3)

    assert calls == [["A", "C"]]
    assert [r["key"] for r in results] == ["a", "b", "c"]
    assert [r["text"] for r in results] == ["譯:A", " ", "譯:C"]
    assert results[1]["_untranslated"] is True
    assert status == "AUTO"


def test_prefix_result_stops_before_first_missing_translation(monkeypatch):
    def partial(batch_items, total, dry_run, *, event_callback=None):
        return [{**batch_items[0], "text": "譯:A"}], "PARTIAL"

    monkeypatch.setattr(main, "_translate_non_blank_batch", partial)
    batch = [_item("A", "a"), _item("", "b"), _item("C", "c")]

    results, status = main.translate_batch_smart(batch, 3)

    assert [r["key"] for r in results] == ["a", "b"]
    assert status == "PARTIAL"


def test_blank_items_do_not_hide_engine_failure(monkeypatch):
    monkeypatch.setattr(
        main,
        "_translate_non_blank_batch",
        lambda *a, **k: ([], "FAILED"),
    )

    results, status = main.translate_batch_smart([_item(""), _item("A")], 2)

    assert results == []
    assert status == "FAILED"


def test_batch_without_blank_items_is_untouched(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_translate_non_blank_batch", _fake_core(calls))
    batch = [_item("A"), _item("B")]

    results, _status = main.translate_batch_smart(batch, 2)

    assert calls == [["A", "B"]]
    assert [r["text"] for r in results] == ["譯:A", "譯:B"]
