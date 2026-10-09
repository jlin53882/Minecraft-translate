"""目錄翻譯與 Mod 資料庫的整合：資料庫 → 快取 → AI，並把結果寫回。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.core import lm_translator, lm_translator_db
from translation_tool.translation_db import (
    KIND_LANG,
    KIND_PATCHOULI,
    DbSettings,
    ScanItem,
    TranslationDB,
    WriteBackItem,
)
from translation_tool.translation_db.schema import SRC_AI, SRC_JAR_TW

BOOK_KEY = "patchouli_books/guide/entries/intro.json#pages[0].text"


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """假的 AI 與快取；回傳 (輸入資料夾, 資料庫路徑, 紀錄)。"""
    inp = tmp_path / "in"
    lang = inp / "assets" / "foo" / "lang" / "en_us.json"
    lang.parent.mkdir(parents=True)
    lang.write_text(
        json.dumps(
            {
                "item.foo.db": "From Database Text",
                "item.foo.cache": "From Cache Text",
                "item.foo.ai": "Needs Machine Translation",
            }
        ),
        encoding="utf-8",
    )
    book = (
        inp
        / "assets"
        / "foo"
        / "patchouli_books"
        / "guide"
        / "en_us"
        / "entries"
        / "intro.json"
    )
    book.parent.mkdir(parents=True)
    book.write_text(
        json.dumps(
            {
                "name": "Intro Page Title",
                "pages": [{"type": "text", "text": "Book paragraph from database."}],
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "mod.db"
    seen: dict[str, list] = {"ai_batches": [], "cache_added": []}

    monkeypatch.setattr(lm_translator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(lm_translator, "reload_translation_cache", lambda: None)
    monkeypatch.setattr(lm_translator, "value_fully_translated", lambda v: bool(v))
    monkeypatch.setattr(lm_translator_db, "value_fully_translated", lambda v: bool(v))
    cache = {"lang": {"item.foo.cache": {"src": "From Cache Text", "dst": "快取譯文"}}}
    monkeypatch.setattr(lm_translator, "get_cache_dict_ref", lambda t: cache.get(t, {}))
    monkeypatch.setattr(
        lm_translator,
        "add_to_cache",
        lambda *a, **k: seen["cache_added"].append(a) or True,
    )
    monkeypatch.setattr(lm_translator, "save_translation_cache", lambda *a, **k: True)
    monkeypatch.setattr(lm_translator, "CHECKPOINT_FILE", str(tmp_path / "ckpt.json"))

    def fake_ai(batch, total=None):
        seen["ai_batches"].append([i["path"] for i in batch])
        return [{**i, "text": f"AI:{i['source_text']}"} for i in batch], "DONE"

    monkeypatch.setattr(lm_translator, "translate_batch_smart", fake_ai)

    def settings(**kw):
        base = {"path": str(db_path), "version": "1.21.1"}
        base.update(kw)
        monkeypatch.setattr(
            lm_translator_db, "load_db_settings", lambda: DbSettings(**base)
        )

    settings()
    return inp, db_path, seen, settings


def _seed(db_path: Path) -> None:
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG, "foo", "item.foo.db", "From Database Text", "資料庫譯文"
            ),
            ScanItem(
                KIND_PATCHOULI,
                "foo",
                BOOK_KEY,
                "Book paragraph from database.",
                "書本譯文",
            ),
        ],
    )
    db.close()


def _run(inp: Path, tmp_path: Path, **kw):
    out = tmp_path / "out"
    list(lm_translator.translate_directory_generator(str(inp), str(out), **kw))
    return out


def test_db_hit_then_cache_then_ai_and_output_is_correct(run_env, tmp_path):
    inp, db_path, seen, _ = run_env
    _seed(db_path)
    out = _run(inp, tmp_path)

    lang = json.loads(
        (out / "assets" / "foo" / "lang" / "zh_tw.json").read_text(encoding="utf-8")
    )
    assert lang["item.foo.db"] == "資料庫譯文"  # 資料庫優先
    assert lang["item.foo.cache"] == "快取譯文"  # 快取
    assert lang["item.foo.ai"] == "AI:Needs Machine Translation"
    # AI 只翻剩下的（書名未被資料庫收錄）；lang 與 patchouli 不混批（各自一批，順序不固定）
    assert sorted(sorted(b) for b in seen["ai_batches"]) == [["item.foo.ai"], ["name"]]
    book = json.loads(
        (
            out
            / "assets"
            / "foo"
            / "patchouli_books"
            / "guide"
            / "en_us"
            / "entries"
            / "intro.json"
        ).read_text(encoding="utf-8")
    )
    assert book["pages"][0]["text"] == "書本譯文"  # Patchouli 也走資料庫


def test_explicit_database_snapshot_survives_global_path_change(run_env, tmp_path):
    """A/B 同版本譯文不同；任務啟動快照固定後，全域路徑改成 B 仍讀 A。"""
    inp, database_a, seen, settings = run_env
    _seed(database_a)
    database_b = tmp_path / "other-mod.db"
    db = TranslationDB(database_b)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "foo",
                "item.foo.db",
                "From Database Text",
                "資料庫 B 譯文",
            ),
            ScanItem(
                KIND_PATCHOULI,
                "foo",
                BOOK_KEY,
                "Book paragraph from database.",
                "B 書本譯文",
            ),
        ],
    )
    db.close()
    settings_a = DbSettings(
        enabled=True,
        path=str(database_a.resolve()),
        version="1.21.1",
    )

    generator = lm_translator.translate_directory_generator(
        str(inp),
        str(tmp_path / "snapshot-output"),
        use_translation_db=True,
        translation_db_version="1.21.1",
        translation_db_settings_snapshot=settings_a,
    )
    assert next(generator) == {"progress": 0.0}
    # Settings can change after the operation starts; this run remains on A.
    settings(path=str(database_b.resolve()))
    list(generator)

    output = tmp_path / "snapshot-output"
    lang = json.loads(
        (output / "assets" / "foo" / "lang" / "zh_tw.json").read_text(encoding="utf-8")
    )
    book = json.loads(
        (
            output
            / "assets"
            / "foo"
            / "patchouli_books"
            / "guide"
            / "en_us"
            / "entries"
            / "intro.json"
        ).read_text(encoding="utf-8")
    )
    assert lang["item.foo.db"] == "資料庫譯文"
    assert book["pages"][0]["text"] == "書本譯文"
    assert "item.foo.db" not in [path for batch in seen["ai_batches"] for path in batch]


def _seed_priority_conflict(db_path: Path) -> tuple[tuple[int, ...], tuple[int, ...]]:
    priority_a = (SRC_JAR_TW, SRC_AI)
    priority_b = (SRC_AI, SRC_JAR_TW)
    db = TranslationDB(db_path, priority=priority_b)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "foo",
                "item.foo.db",
                "From Database Text",
                "模組自帶譯文",
                source=SRC_JAR_TW,
            ),
            ScanItem(
                KIND_LANG,
                "foo",
                "item.foo.db",
                "From Database Text",
                "AI 優先譯文",
                source=SRC_AI,
            ),
        ],
    )
    db.close()
    return priority_a, priority_b


def test_lm_snapshot_open_does_not_rewrite_global_priority(run_env, tmp_path):
    _inp, db_path, _seen, _settings = run_env
    priority_a, priority_b = _seed_priority_conflict(db_path)

    ctx = lm_translator_db.open_directory_db(
        tmp_path,
        settings_snapshot=DbSettings(
            path=str(db_path.resolve()), version="1.21.1", priority=priority_a
        ),
    )
    assert ctx is not None
    try:
        stored_priority = ctx.db._one("SELECT value FROM meta WHERE key='priority'")[0]
        entry_id = ctx.db._one(
            "SELECT id FROM entry WHERE mod_id='foo' AND key='item.foo.db'"
        )[0]
        assert stored_priority == ",".join(map(str, priority_b))
        assert ctx.db.get_entry(entry_id).zh_tw == "AI 優先譯文"
    finally:
        ctx.close()


def test_lm_resolver_uses_snapshot_after_another_operation_rebuilds_effective(
    run_env, tmp_path
):
    _inp, db_path, _seen, _settings = run_env
    priority_a, priority_b = _seed_priority_conflict(db_path)
    ctx = lm_translator_db.open_directory_db(
        tmp_path,
        settings_snapshot=DbSettings(
            path=str(db_path.resolve()), version="1.21.1", priority=priority_a
        ),
    )
    assert ctx is not None
    other_operation = None
    try:
        other_operation = TranslationDB(db_path, priority=priority_b)
        hit = ctx.resolver.lookup(KIND_LANG, "foo", "item.foo.db", "From Database Text")
        assert hit is not None
        assert hit.zh_tw == "模組自帶譯文"
    finally:
        if other_operation is not None:
            other_operation.close()
        ctx.close()


def test_lm_writeback_preserves_global_effective_priority_and_reviewed_translation(
    run_env, tmp_path
):
    _inp, db_path, _seen, _settings = run_env
    priority_a = (SRC_JAR_TW, SRC_AI)
    priority_b = (SRC_AI, SRC_JAR_TW)
    db = TranslationDB(db_path, priority=priority_b)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "foo",
                "item.foo.ai",
                "Needs Machine Translation",
                "既有模組譯文",
                source=SRC_JAR_TW,
            ),
            ScanItem(
                KIND_LANG,
                "foo",
                "item.foo.reviewed",
                "Reviewed Text",
                "人工審核譯文",
                source=SRC_JAR_TW,
            ),
            ScanItem(
                KIND_LANG,
                "foo",
                "item.foo.reviewed",
                "Reviewed Text",
                "未審核 AI 候選",
                source=SRC_AI,
            ),
        ],
    )
    reviewed_id = db._one(
        "SELECT id FROM entry WHERE mod_id='foo' AND key='item.foo.reviewed'"
    )[0]
    with db._tx() as conn:
        conn.execute(
            "UPDATE translation SET checker='reviewed' WHERE entry_id=? AND source=?",
            (reviewed_id, SRC_JAR_TW),
        )
        db._refresh(conn, [reviewed_id])
    db.close()

    ctx = lm_translator_db.open_directory_db(
        tmp_path,
        settings_snapshot=DbSettings(
            path=str(db_path.resolve()),
            version="1.21.1",
            priority=priority_a,
            write_back=True,
        ),
    )
    assert ctx is not None and ctx.buffer is not None
    try:
        ctx.db.write_back(
            "1.21.1",
            [
                WriteBackItem(
                    KIND_LANG,
                    "foo",
                    "item.foo.ai",
                    "Needs Machine Translation",
                    "新 AI 譯文",
                )
            ],
            fill_other_versions=False,
        )
        stored_priority = ctx.db._one("SELECT value FROM meta WHERE key='priority'")[0]
        ai_id = ctx.db._one(
            "SELECT id FROM entry WHERE mod_id='foo' AND key='item.foo.ai'"
        )[0]
        assert stored_priority == ",".join(map(str, priority_b))
        assert ctx.db.get_entry(ai_id).zh_tw == "新 AI 譯文"
        assert ctx.db.get_entry(reviewed_id).zh_tw == "人工審核譯文"
    finally:
        ctx.close()


def test_results_are_written_back_without_overwriting(run_env, tmp_path):
    inp, db_path, _seen, _ = run_env
    _seed(db_path)
    _run(inp, tmp_path)

    db = TranslationDB(db_path)
    rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
    assert (
        rows["item.foo.db"].zh_tw == "資料庫譯文"
        and rows["item.foo.db"].source == SRC_JAR_TW
    )  # 沒被覆蓋
    assert (rows["item.foo.ai"].zh_tw, rows["item.foo.ai"].source) == (
        "AI:Needs Machine Translation",
        SRC_AI,
    )
    assert rows["item.foo.cache"].zh_tw == "快取譯文"  # 快取命中也回補資料庫
    db.close()


def test_write_back_fills_blank_in_other_versions(run_env, tmp_path):
    inp, db_path, _seen, _ = run_env
    db = TranslationDB(db_path)
    db.ingest(
        "1.20.1",
        [ScanItem(KIND_LANG, "foo", "item.foo.ai", "Needs Machine Translation")],
    )
    db.close()
    _run(inp, tmp_path)
    db = TranslationDB(db_path)
    old = db.list_entries("1.20.1")[0][0]
    assert (old.zh_tw, old.source) == ("AI:Needs Machine Translation", SRC_AI)
    db.close()


def test_cross_version_translation_is_reused(run_env, tmp_path):
    inp, db_path, seen, _ = run_env
    db = TranslationDB(db_path)
    db.ingest(
        "1.20.1",
        [
            ScanItem(
                KIND_LANG, "foo", "item.foo.ai", "Needs Machine Translation", "舊版譯文"
            )
        ],
    )
    db.close()
    _run(inp, tmp_path)
    assert "item.foo.ai" not in [p for b in seen["ai_batches"] for p in b]


def test_cross_version_can_be_disabled(run_env, tmp_path):
    inp, db_path, seen, settings = run_env
    settings(cross_version=False)
    db = TranslationDB(db_path)
    db.ingest(
        "1.20.1",
        [
            ScanItem(
                KIND_LANG, "foo", "item.foo.ai", "Needs Machine Translation", "舊版譯文"
            )
        ],
    )
    db.close()
    _run(inp, tmp_path)
    assert "item.foo.ai" in [p for b in seen["ai_batches"] for p in b]


def test_changed_english_is_not_reused(run_env, tmp_path):
    inp, db_path, seen, _ = run_env
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "foo", "item.foo.db", "Old English Text", "舊譯文")],
    )
    db.close()
    out = _run(inp, tmp_path)
    assert "item.foo.db" in [p for b in seen["ai_batches"] for p in b]
    lang = json.loads(
        (out / "assets" / "foo" / "lang" / "zh_tw.json").read_text(encoding="utf-8")
    )
    assert lang["item.foo.db"] == "AI:From Database Text"


def test_disabled_missing_version_or_missing_file_leaves_flow_untouched(
    run_env, tmp_path
):
    inp, db_path, seen, settings = run_env
    _seed(db_path)
    settings(enabled=False)
    _run(inp, tmp_path)
    assert "item.foo.db" in [p for b in seen["ai_batches"] for p in b]

    seen["ai_batches"].clear()
    settings(version="")
    _run(inp, tmp_path)
    assert "item.foo.db" in [p for b in seen["ai_batches"] for p in b]

    seen["ai_batches"].clear()
    settings(path=str(tmp_path / "absent.db"))
    _run(inp, tmp_path)
    assert not (tmp_path / "absent.db").exists()  # 翻譯流程不會憑空建立資料庫
    assert "item.foo.db" in [p for b in seen["ai_batches"] for p in b]


def test_explicit_arguments_override_settings(run_env, tmp_path):
    inp, db_path, seen, settings = run_env
    _seed(db_path)
    settings(enabled=False, version="")
    _run(inp, tmp_path, use_translation_db=True, translation_db_version="1.21.1")
    assert "item.foo.db" not in [p for b in seen["ai_batches"] for p in b]


def test_write_back_can_be_turned_off_and_dry_run_never_writes(run_env, tmp_path):
    inp, db_path, _seen, settings = run_env
    _seed(db_path)
    settings(write_back=False)
    _run(inp, tmp_path)
    db = TranslationDB(db_path)
    assert db.count_entries() == 2  # 只有種子資料
    db.close()

    settings(write_back=True)
    _run(inp, tmp_path / "dry", dry_run=True)
    db = TranslationDB(db_path)
    assert db.count_entries() == 2
    db.close()
