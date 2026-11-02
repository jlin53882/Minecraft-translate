"""目錄翻譯在資料庫開啟時也要帶入已審核術語（只注入，不改批次分組）。"""

from __future__ import annotations

from translation_tool.core import lm_moddb_context as ctx


def _items():
    return [
        {
            "path": "a",
            "text": "Iron Gear",
            "source_text": "Iron Gear",
            "_mod_id": "m",
            "cache_type": "lang",
        },
        {
            "path": "b",
            "text": "Wooden Gear",
            "source_text": "Wooden Gear",
            "_mod_id": "m",
            "cache_type": "lang",
        },
    ]


def _config(monkeypatch, enabled):
    monkeypatch.setattr(
        ctx,
        "load_config",
        lambda: {"lm_translator": {"moddb_reviewed_glossary": enabled}},
    )


def test_attaches_glossary_without_touching_batch_grouping(monkeypatch):
    _config(monkeypatch, True)
    calls = []

    def loader(mod_id, version, sources):
        calls.append((mod_id, version, tuple(sources)))
        return [{"en": "Gear", "zh_tw": "齒輪"}]

    items = _items()
    ctx.attach_reviewed_glossary(
        items, mc_version="1.21.1", reviewed_glossary_loader=loader
    )

    assert len(calls) == 1  # 同模組、同版本只查一次
    assert calls[0][:2] == ("m", "1.21.1")
    assert all(item["_reviewed_glossary"][0]["zh_tw"] == "齒輪" for item in items)
    assert all("_batch_group" not in item for item in items)
    assert all("_translation_session" not in item for item in items)


def test_setting_off_clears_previous_glossary(monkeypatch):
    _config(monkeypatch, False)
    items = _items()
    items[0]["_reviewed_glossary"] = [{"en": "x", "zh_tw": "y"}]
    ctx.attach_reviewed_glossary(
        items, mc_version="1.21.1", reviewed_glossary_loader=lambda *a: [{"en": "a"}]
    )

    assert "_reviewed_glossary" not in items[0]
    assert "_reviewed_glossary" not in items[1]


def test_directory_phase_injects_glossary_only_when_a_database_is_open(monkeypatch):
    from types import SimpleNamespace

    from translation_tool.core import lm_translator

    _config(monkeypatch, True)
    monkeypatch.setattr(ctx, "load_config", lambda: {"lm_translator": {}})
    db = SimpleNamespace(
        reviewed_lang_glossary=lambda *a: [{"en": "Gear", "zh_tw": "齒輪"}]
    )
    items = _items()
    lm_translator._inject_reviewed_glossary(items, None)
    assert "_reviewed_glossary" not in items[0]

    lm_translator._inject_reviewed_glossary(
        items, SimpleNamespace(db=db, version="1.21.1")
    )
    assert items[0]["_reviewed_glossary"][0]["zh_tw"] == "齒輪"
