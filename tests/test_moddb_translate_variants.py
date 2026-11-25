"""批次機翻「包含其他原文」：沒有譯文的其他原文也成為候選，翻完存回其他原文。"""

# 從 test_moddb_translate 匯入 fixture（db_path、fake_cache）：函式參數會「重新定義」匯入的名稱
# ruff: noqa: F811

from __future__ import annotations

from app.services_impl.moddb_translate_service import (
    TranslateOptions,
    estimate_scope_plan,
)
from app.services_impl.moddb_translate_variants import (
    count_scope_untranslated,
    is_variant_row,
    variant_candidate_rows,
)
from tests.test_moddb_translate import (  # noqa: F401
    _known_lang_item,
    db_path,
    fake_cache,
    fake_engine,
    run,
)
from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.schema import KIND_LANG, SRC_AI, SRC_JAR_TW

VERSION = "1.21.1"


def _seed(path):
    """foo.a：主原文有譯文，另有兩份其他原文（一份已有模組自帶譯文、一份沒有）；foo.b：沒有譯文。"""
    db = TranslationDB(path)
    db.ingest(
        VERSION,
        [
            _known_lang_item("foo", "item.foo.a", "Show Background", "顯示背景"),
            _known_lang_item("foo", "item.foo.b", "Infused Alloy"),
        ],
    )
    # 模組更新：同鍵值出現新原文
    db.ingest(
        VERSION, [_known_lang_item("foo", "item.foo.a", "Background Style", "背景樣式")]
    )
    db.ingest(VERSION, [_known_lang_item("foo", "item.foo.a", "Draw Layer")])
    db.close()


def _db(path):
    return TranslationDB(path)


def test_default_option_ignores_other_originals(db_path, monkeypatch):
    _seed(db_path)
    fake_engine(monkeypatch, lambda t: "翻:" + t)
    summary = run(TranslateOptions(version=VERSION))["summary"]
    assert summary["written"] == 1 and summary["variant_candidates"] == 0
    db = _db(db_path)
    try:
        assert db.count_untranslated_variants(VERSION) == 1  # 其他原文仍沒有譯文
    finally:
        db.close()


def test_including_other_originals_translates_and_stores_them_as_other_originals(
    db_path, monkeypatch
):
    _seed(db_path)
    fake_engine(monkeypatch, lambda t: "翻:" + t)
    snap = run(TranslateOptions(version=VERSION, include_variants=True))
    summary = snap["summary"]
    assert summary["written"] == 2 and summary["variant_written"] == 1
    assert summary["variant_candidates"] == 1 and summary["remaining"] == 0
    db = _db(db_path)
    try:
        # 其他原文的譯文存在側表，來源 AI 機翻
        assert db._q(
            "SELECT v.en_us, t.zh_tw, t.source FROM entry_variant v "
            "JOIN variant_translation t ON t.variant_id = v.id ORDER BY v.en_us"
        ) == [
            ("Background Style", "背景樣式", SRC_JAR_TW),
            ("Draw Layer", "翻:Draw Layer", SRC_AI),
        ]
        # 主原文與生效譯文不動
        rows = {r.key: r for r in db.list_entries(VERSION)[0]}
        assert (
            rows["item.foo.a"].zh_tw == "顯示背景"
            and rows["item.foo.a"].en_us == "Show Background"
        )
        assert rows["item.foo.b"].zh_tw == "翻:Infused Alloy"
        assert db.count_untranslated_variants(VERSION) == 0
    finally:
        db.close()


def test_preview_counts_match_the_real_run(db_path, monkeypatch):
    _seed(db_path)
    fake_engine(monkeypatch, lambda t: "翻:" + t)
    preview = run(
        TranslateOptions(version=VERSION, include_variants=True, dry_run=True)
    )["summary"]
    assert preview["ai_items"] == 2 and preview["variant_candidates"] == 1
    assert preview["remaining"] == 2  # 條目 1 ＋ 其他原文 1
    db = _db(db_path)
    try:
        assert db.count_untranslated_variants(VERSION) == 1  # 預覽不寫入
        ai, batches = estimate_scope_plan(db, version=VERSION, include_variants=True)
        assert ai == 2 and batches >= 1
        assert estimate_scope_plan(db, version=VERSION)[0] == 1
    finally:
        db.close()


def test_a_variant_sharing_the_original_with_an_entry_is_sent_to_ai_once(
    db_path, monkeypatch
):
    """其他原文與另一個條目的原文相同：合併成一組，只送 AI 一次，兩邊都寫入。"""
    db = TranslationDB(db_path)
    db.ingest(
        VERSION,
        [
            _known_lang_item("foo", "item.foo.a", "Old Text"),
            _known_lang_item("foo", "item.foo.z", "Shared Text"),
        ],
    )
    db.ingest(VERSION, [_known_lang_item("foo", "item.foo.a", "Shared Text")])
    db.close()
    sent: list[str] = []

    def translator(text):
        sent.append(text)
        return "共用譯文"

    fake_engine(monkeypatch, translator)
    summary = run(TranslateOptions(version=VERSION, include_variants=True))["summary"]
    assert sent.count("Shared Text") == 1
    assert summary["variant_candidates"] == 1
    db = _db(db_path)
    try:
        assert db._q("SELECT zh_tw FROM variant_translation") == [("共用譯文",)]
        rows = {r.key: r for r in db.list_entries(VERSION)[0]}
        assert rows["item.foo.z"].zh_tw == "共用譯文"
    finally:
        db.close()


def test_quality_rejection_of_a_variant_is_reported_but_not_openable(
    db_path, monkeypatch
):
    """其他原文的譯文特殊字元不一致：不寫入，不列進可在條目校對開啟的清單。"""
    db = TranslationDB(db_path)
    db.ingest(VERSION, [_known_lang_item("foo", "item.foo.a", "Value: %s", "值：%s")])
    db.ingest(VERSION, [_known_lang_item("foo", "item.foo.a", "Value: %s (%d)")])
    db.close()
    fake_engine(monkeypatch, lambda t: "值：%s")  # 少了 %d
    summary = run(TranslateOptions(version=VERSION, include_variants=True))["summary"]
    assert summary["written"] == 0 and summary["flagged"] == 1
    assert summary["flagged_entries"] == {}
    db = _db(db_path)
    try:
        assert db.count_untranslated_variants(VERSION) == 1
    finally:
        db.close()


def test_limit_counts_groups_and_entries_come_first(db_path, monkeypatch):
    """單次上限計組數；條目排在其他原文前面。"""
    db = TranslationDB(db_path)
    db.ingest(
        VERSION,
        [
            _known_lang_item("foo", "item.foo.a", "Alpha Entry"),
            _known_lang_item("foo", "item.foo.b", "Bravo Entry"),
            _known_lang_item("foo", "item.foo.c", "Old"),
        ],
    )
    db.ingest(VERSION, [_known_lang_item("foo", "item.foo.c", "New Variant Text")])
    db.close()
    sent: list[str] = []
    fake_engine(monkeypatch, lambda t: sent.append(t) or "譯")
    run(TranslateOptions(version=VERSION, include_variants=True, limit=2))
    assert sent == ["Alpha Entry", "Bravo Entry"]  # 條目先用完額度


def test_candidate_helpers_and_scope_counts(db_path):
    _seed(db_path)
    db = _db(db_path)
    try:
        rows, locales = variant_candidate_rows(db, VERSION, [], None)
        assert (
            len(rows) == 1 and is_variant_row(rows[0][0]) and rows[0][4] == "Draw Layer"
        )
        assert locales[rows[0][0]] == "en_us"  # 沿用所屬條目的來源語系
        assert (
            count_scope_untranslated(db, VERSION, [], None, include_variants=False) == 1
        )
        assert (
            count_scope_untranslated(db, VERSION, [], None, include_variants=True) == 2
        )
        assert (
            count_scope_untranslated(db, VERSION, ["nope"], None, include_variants=True)
            == 0
        )
        assert db.count_untranslated_variants(VERSION, kind="guide") == 0
        assert db.untranslated_variant_entries(VERSION, ["foo"], kind=KIND_LANG)
    finally:
        db.close()


def test_old_schema_without_variant_tables_yields_no_candidates(db_path):
    import sqlite3

    db = TranslationDB(db_path)
    db.ingest(VERSION, [_known_lang_item("foo", "item.foo.a", "Alpha")])
    db.close()
    raw = sqlite3.connect(db_path)
    raw.executescript(
        "DROP TABLE variant_translation; DROP TABLE entry_variant; "
        "UPDATE meta SET value='17' WHERE key='schema_version';"
    )
    raw.commit()
    raw.close()
    readonly = TranslationDB(db_path, readonly=True)
    try:
        assert readonly.count_untranslated_variants(VERSION) == 0
        assert readonly.untranslated_variant_entries(VERSION) == []
    finally:
        readonly.close()


def test_variant_without_translation_is_the_same_set_the_list_will_use(db_path):
    """「沒有譯文」只有一個定義（``variants.UNTRANSLATED_SQL``）：已有任何非空譯文的不算。"""
    _seed(db_path)
    db = _db(db_path)
    try:
        with db._tx() as conn:
            vid = conn.execute(
                "SELECT id FROM entry_variant WHERE en_us='Draw Layer'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO variant_translation (variant_id, source, zh_tw) VALUES (?, 0, '')",
                (vid,),
            )  # 空字串譯文不算有譯文
        assert db.count_untranslated_variants(VERSION) == 1
        with db._tx() as conn:
            conn.execute(
                "INSERT INTO variant_translation (variant_id, source, zh_tw) VALUES (?, 6, '人工')",
                (vid,),
            )
        assert db.count_untranslated_variants(VERSION) == 0
    finally:
        db.close()


def test_other_versions_and_other_scopes_are_not_included(db_path):
    """只取目標版本、所選模組與類型內的其他原文。"""
    _seed(db_path)
    db = _db(db_path)
    try:
        db.ingest("1.20.1", [_known_lang_item("foo", "item.foo.q", "Old Q")])
        db.ingest("1.20.1", [_known_lang_item("foo", "item.foo.q", "New Q")])
        db.ingest(VERSION, [_known_lang_item("bar", "item.bar.a", "Bar Old")])
        db.ingest(VERSION, [_known_lang_item("bar", "item.bar.a", "Bar New")])
        assert (
            db.count_untranslated_variants(VERSION) == 2
        )  # foo 的 Draw Layer、bar 的 Bar New
        assert db.count_untranslated_variants(VERSION, ["bar"]) == 1
        assert db.count_untranslated_variants("1.20.1") == 1
        assert [r[5] for r in db.untranslated_variant_entries(VERSION, ["foo"])] == [
            "Draw Layer"
        ]
        assert db.count_untranslated_variants(VERSION, kind="guide") == 0
    finally:
        db.close()


def test_translate_panel_switch_feeds_options_scope_key_and_counts(db_path):
    """面板開關：預設關閉；勾選後進 TranslateOptions、預覽範圍鍵，並把其他原文算進範圍筆數。"""
    from app.views.moddb import translate_panel
    from tests.conftest import mock_page

    _seed(db_path)
    db = _db(db_path)
    try:
        panel = translate_panel.TranslatePanel(mock_page(), lambda: db)
        panel.refresh_scope()
        assert panel.variants_row.value is False
        assert panel.build_options().include_variants is False
        off_key = panel._scope_key()
        assert (
            "1" in panel.count_text.value
            and "2" not in panel.count_text.value.split("筆")[0]
        )
        panel.variants_row.value = True
        panel._refresh_counts()
        assert panel.build_options().include_variants is True
        assert panel._scope_key() != off_key  # 預覽批數只適用開關相同時
        assert panel._last_counts[0] == 2  # 條目 1 ＋ 其他原文 1
    finally:
        db.close()


def test_panel_refresh_snapshot_counts_variants_only_when_asked(db_path):
    from app.views.moddb import panel_refresh

    _seed(db_path)
    db = _db(db_path)
    try:
        base = {
            "version": VERSION,
            "pack": None,
            "mod": "",
            "kind": None,
            "reuse": False,
        }
        off = panel_refresh._load_translate_snapshot(db, base, {})
        on = panel_refresh._load_translate_snapshot(db, {**base, "variants": True}, {})
        assert off["missing"] == 1 and on["missing"] == 2
        assert on["merged_groups"] == off["merged_groups"] + 1
    finally:
        db.close()


def test_unlimited_scope_includes_modules_that_only_have_other_originals(
    db_path, monkeypatch
):
    """單次上限 0（不限）：沒有未翻譯條目、只有其他原文的模組也要送，預覽與實際一致。"""
    db = TranslationDB(db_path)
    db.ingest(
        VERSION,
        [
            _known_lang_item("foo", "item.foo.a", "Untranslated"),
            _known_lang_item("bar", "item.bar.a", "Old Text", "舊文字"),
        ],
    )
    db.ingest(VERSION, [_known_lang_item("bar", "item.bar.a", "New Text")])
    db.close()
    sent: list[str] = []
    fake_engine(monkeypatch, lambda t: sent.append(t) or "譯:" + t)
    options = TranslateOptions(version=VERSION, include_variants=True, limit=0)
    preview = estimate_scope_plan(
        _db(db_path), version=VERSION, limit=0, include_variants=True
    )
    assert preview[0] == 2  # 預覽也算進只有其他原文的模組
    summary = run(options)["summary"]
    assert sorted(sent) == ["New Text", "Untranslated"]
    assert summary["variant_written"] == 1 and summary["remaining"] == 0


def test_trivial_untranslated_variants_are_purged_when_the_db_opens(db_path):
    """與主原文只差大小寫的其他原文（升級回填留下）沒有譯文，開啟資料庫時清掉。"""
    db = TranslationDB(db_path)
    db.ingest(
        VERSION,
        [
            _known_lang_item("foo", "item.foo.a", "Show Background", "顯示背景"),
            _known_lang_item("foo", "item.foo.b", "Old Text"),
            _known_lang_item("foo", "item.foo.c", "CAFÉ"),
        ],
    )
    db.ingest(VERSION, [_known_lang_item("foo", "item.foo.b", "Real Change")])
    for key, text in (("item.foo.a", "Show background"), ("item.foo.c", "café")):
        entry_id = db._q("SELECT id FROM entry WHERE key=?", (key,))[0][0]
        db._conn.execute(
            "INSERT INTO entry_variant (entry_id, en_us, first_seen, last_seen) "
            "VALUES (?, ?, 't', 't')",
            (entry_id, text),
        )
    db._conn.commit()
    db.close()
    reopened = TranslationDB(db_path)
    try:
        assert reopened._q("SELECT en_us FROM entry_variant ORDER BY en_us") == [
            ("Real Change",)
        ]  # 只差大小寫（含非 ASCII）的清掉，真正的原文變動留著
    finally:
        reopened.close()


def test_an_empty_ai_translation_row_is_filled_by_the_batch_run(db_path, monkeypatch):
    """變體已有一列空字串的 AI 譯文（算沒有譯文）：機翻要補上內容，不能每次重送。"""
    _seed(db_path)
    db = _db(db_path)
    try:
        vid = db._q("SELECT id FROM entry_variant WHERE en_us='Draw Layer'")[0][0]
        db._conn.execute(
            "INSERT INTO variant_translation (variant_id, source, zh_tw) VALUES (?, ?, '')",
            (vid, SRC_AI),
        )
        db._conn.commit()
    finally:
        db.close()
    fake_engine(monkeypatch, lambda t: "翻:" + t)
    summary = run(TranslateOptions(version=VERSION, include_variants=True))["summary"]
    assert summary["variant_written"] == 1 and summary["remaining"] == 0
    db = _db(db_path)
    try:
        assert db._q(
            "SELECT t.zh_tw FROM variant_translation t "
            "JOIN entry_variant v ON v.id = t.variant_id "
            "WHERE v.en_us='Draw Layer' AND t.source=?",
            (SRC_AI,),
        ) == [("翻:Draw Layer",)]
        assert db.count_untranslated_variants(VERSION) == 0
    finally:
        db.close()
