"""資料庫清理的安全契約：預覽 token、還原、指紋、檢查檔內容與輸入驗證。

只用暫存資料庫與合成資料，不碰任何真實資料庫。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.cleanup import (
    CleanupPlanChanged,
    _write_report,
)
from translation_tool.translation_db.models import ScanItem

VERSION = "1.21.1"
LICENSE = "Permission is hereby granted, free of charge, to any person."
TAGS_ONLY = '<ImportStructure src="../s.snbt"></ImportStructure>'
TABLES = ("entry", "translation", "effective", "history", "src_change")


def _db(tmp_path: Path, name: str = "t.db") -> TranslationDB:
    db = TranslationDB(tmp_path / name)
    db.ingest(
        VERSION,
        [
            ScanItem("guide", "m", "g/a.md#0", TAGS_ONLY),
            ScanItem("guide", "m", "g/a.md#1", LICENSE),
            ScanItem("guide", "m", "g/a.md#2", "Open the terminal with a right click."),
            ScanItem("lang", "m", "item.m.a", "Alpha"),
        ],
    )
    return db


def _entry(db: TranslationDB, key: str) -> int:
    return db._one("SELECT id FROM entry WHERE key = ?", (key,))[0]


def _rule_report(db: TranslationDB, tmp_path: Path, name: str = "r.json") -> Path:
    db.export_cleanup_report(VERSION, tmp_path / name)
    return tmp_path / name


def _count(db: TranslationDB, table: str = "entry") -> int:
    return db._one(f"SELECT COUNT(*) FROM {table}")[0]


def _dump(db: TranslationDB) -> dict[str, list]:
    """整個資料庫的內容（所有資料表、所有欄位），用來比對「刪除再還原後完全一樣」。"""
    return {
        t: [list(r) for r in db._q(f"SELECT * FROM {t} ORDER BY 1, 2")] for t in TABLES
    }


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- A1 預覽 token
def test_apply_without_or_with_a_wrong_token_deletes_nothing(tmp_path):
    db = _db(tmp_path)
    path = _rule_report(db, tmp_path)
    before = _count(db)
    for token in ("", "0" * 64):
        with pytest.raises(CleanupPlanChanged):
            db.apply_cleanup(path, token)
    assert _count(db) == before


def test_token_of_another_report_cannot_confirm_this_one(tmp_path):
    db = _db(tmp_path)
    path_a = _rule_report(db, tmp_path, "a.json")
    path_b = _rule_report(db, tmp_path, "b.json")
    data = _read(path_b)
    data["items"] = data["items"][:1]  # B 只刪一筆
    _write(path_b, data)
    token_b = db.preview_cleanup(path_b).token
    before = _count(db)
    with pytest.raises(CleanupPlanChanged):
        db.apply_cleanup(path_a, token_b)  # 先預覽 B 再對 A 套用
    assert _count(db) == before


def test_report_edited_after_the_preview_is_rejected(tmp_path):
    db = _db(tmp_path)
    path = _rule_report(db, tmp_path)
    data = _read(path)
    data["items"][0]["delete"] = False
    _write(path, data)
    plan = db.preview_cleanup(path)
    data["items"][0]["delete"] = True  # 預覽後又改成要刪：範圍變大
    _write(path, data)
    before = _count(db)
    with pytest.raises(CleanupPlanChanged):
        db.apply_cleanup(path, plan.token)
    assert _count(db) == before


def test_data_changed_after_the_preview_is_rejected(tmp_path):
    db = _db(tmp_path)
    path = _rule_report(db, tmp_path)
    plan = db.preview_cleanup(path)
    db.ingest(VERSION, [ScanItem("guide", "m", "g/a.md#1", LICENSE, zh_tw="授權")])
    before = _count(db)
    with pytest.raises(CleanupPlanChanged):
        db.apply_cleanup(path, plan.token)
    assert _count(db) == before


def test_report_from_another_database_is_rejected(tmp_path):
    first = _db(tmp_path, "first.db")
    second = _db(tmp_path, "second.db")  # 內容與編號完全相同的另一個資料庫
    path = _rule_report(first, tmp_path)
    second.export_cleanup_report(VERSION, tmp_path / "other.json")  # 讓它也有身分
    with pytest.raises(ValueError, match="不是從目前的資料庫匯出"):
        second.preview_cleanup(path)


# ---------------------------------------------------------------- A2／A3 還原
def _protected_path(db: TranslationDB, tmp_path: Path) -> Path:
    path = tmp_path / "p.json"
    db.export_protected_report(VERSION, path)
    data = _read(path)
    for item in data["items"]:
        item["delete"] = True
    _write(path, data)
    return path


def _apply(db: TranslationDB, path: Path):
    return db.apply_cleanup(path, db.preview_cleanup(path).token)


def test_revert_survives_history_and_translation_ids_reused_by_new_data(tmp_path):
    db = _db(tmp_path)
    victim = _entry(db, "g/a.md#1")
    db.ingest(VERSION, [ScanItem("guide", "m", "g/a.md#1", LICENSE, zh_tw="授權條款")])
    db.save_manual(victim, "授權條款（人工）")
    path = _protected_path(db, tmp_path)
    plan = _apply(db, path)
    assert plan.deletable >= 1
    # 刪除後別的條目寫入新譯文與歷史，回收了被刪條目用過的最大編號
    db.save_manual(_entry(db, "item.m.a"), "甲")
    reused = db._one("SELECT MAX(id) FROM history")[0]
    snapshot_ids = {
        json.loads(db._one("SELECT payload FROM cleanup_batch")[0])["history"]["rows"][
            0
        ][0]
    }
    assert reused in snapshot_ids  # 確實撞到了
    result = db.revert_cleanup(plan.batch_id)
    assert result.complete and result.conflicts == []
    restored = _entry(db, "g/a.md#1")
    texts = {
        r[0]
        for r in db._q("SELECT zh_tw FROM translation WHERE entry_id = ?", (restored,))
    }
    assert "授權條款（人工）" in texts
    assert (
        db._one("SELECT COUNT(*) FROM history WHERE entry_id = ?", (restored,))[0] >= 1
    )
    assert db._one("PRAGMA foreign_key_check") is None
    assert db._one("PRAGMA integrity_check")[0] == "ok"


def test_revert_gives_back_exactly_what_was_deleted(tmp_path):
    db = _db(tmp_path)
    victim = _entry(db, "g/a.md#1")
    db.ingest(VERSION, [ScanItem("guide", "m", "g/a.md#1", LICENSE, zh_tw="授權條款")])
    db.save_manual(victim, "授權條款（人工）")
    before = _dump(db)
    plan = _apply(db, _protected_path(db, tmp_path))
    assert _dump(db) != before
    db.revert_cleanup(plan.batch_id)
    assert _dump(db) == before  # 含 revision、審核狀態、checker、時間、歷史與生效譯文
    assert db._one("PRAGMA foreign_key_check") is None


def test_src_change_rows_go_with_the_entry_and_come_back(tmp_path):
    db = _db(tmp_path)
    db.ingest(VERSION, [ScanItem("guide", "m", "g/a.md#1", LICENSE + " v2")])
    assert _count(db, "src_change") == 1
    path = _rule_report(db, tmp_path)
    before = _dump(db)
    plan = _apply(db, path)
    assert _count(db, "src_change") == 0  # 條目沒了，「原文變更」不留成孤兒
    db.revert_cleanup(plan.batch_id)
    assert _dump(db) == before


def test_partial_revert_stays_open_until_every_entry_is_back(tmp_path):
    db = _db(tmp_path)
    path = _rule_report(db, tmp_path)
    plan = _apply(db, path)
    db.ingest(VERSION, [ScanItem("guide", "m", "g/a.md#1", LICENSE)])  # 重新掃描
    first = db.revert_cleanup(plan.batch_id)
    assert not first.complete and first.pending == 1 and len(first.conflicts) == 1
    info = db.cleanup_batches()[0]
    assert info.reverted_at is None and info.restored == first.restored
    assert info.pending == 1
    # 衝突的那筆被處理掉之後可以再還原，已經還原的不會重複寫入
    db._conn.execute("DELETE FROM entry WHERE key = 'g/a.md#1'")
    db._conn.commit()
    second = db.revert_cleanup(plan.batch_id)
    assert second.complete and second.restored == 1
    assert db.cleanup_batches()[0].complete
    with pytest.raises(ValueError):
        db.revert_cleanup(plan.batch_id)


def test_older_unfinished_batches_are_listed_newest_first(tmp_path):
    db = _db(tmp_path)
    path = _rule_report(db, tmp_path)
    first = _apply(db, path)
    db.ingest(VERSION, [ScanItem("guide", "m", "g/z.md#0", TAGS_ONLY)])
    second = _apply(db, _rule_report(db, tmp_path, "r2.json"))
    db.revert_cleanup(second.batch_id)
    batches = db.cleanup_batches()
    assert [b.batch_id for b in batches] == [second.batch_id, first.batch_id]
    assert batches[0].complete and not batches[1].complete
    assert db.last_cleanup_batch().batch_id == second.batch_id


# ---------------------------------------------------------------- B1 完整指紋
def _state(db: TranslationDB, key: str) -> str:
    entry_id = _entry(db, key)
    return db._translation_states([entry_id])[entry_id]


@pytest.mark.parametrize(
    "mutate",
    [
        "UPDATE translation SET checker = 'someone'",
        "UPDATE translation SET review_status = 'reviewed'",
        "UPDATE translation SET zh_cn = '简体'",
        "UPDATE history SET new_zh_tw = 'tampered'",
        "UPDATE effective SET review_status = 'reviewed'",
    ],
)
def test_state_fingerprint_changes_for_every_kind_of_edit(tmp_path, mutate):
    db = _db(tmp_path)
    db.save_manual(_entry(db, "item.m.a"), "甲")
    before = _state(db, "item.m.a")
    assert len(before) == 64  # 完整 SHA-256
    db._conn.execute(mutate)
    db._conn.commit()
    assert _state(db, "item.m.a") != before


def test_state_fingerprint_is_stable_and_follows_source_priority(tmp_path):
    db = _db(tmp_path)
    db.ingest(VERSION, [ScanItem("lang", "m", "item.m.a", "Alpha", zh_tw="甲")])
    db.save_manual(_entry(db, "item.m.a"), "乙")
    assert _state(db, "item.m.a") == _state(db, "item.m.a")
    before = _state(db, "item.m.a")
    db.set_priority(tuple(reversed(db.priority)))  # 優先序改了，生效譯文跟著變
    assert _state(db, "item.m.a") != before


# ---------------------------------------------------------------- A5 揭露所有資料
def test_protected_report_lists_every_source_and_history(tmp_path):
    db = _db(tmp_path)
    entry_id = _entry(db, "g/a.md#1")
    db.ingest(
        VERSION,
        [
            ScanItem("guide", "m", "g/a.md#1", LICENSE, zh_tw="授權條款"),
            ScanItem("guide", "m", "g/a.md#1", LICENSE, zh_tw="授權 150", source=150),
        ],
    )
    db.save_manual(entry_id, "人工授權")
    path = tmp_path / "p.json"
    report = db.export_protected_report(VERSION, path)
    item = next(i for i in _read(path)["items"] if i["key"] == "g/a.md#1")
    by_code = {s["code"]: s for s in item["sources"]}
    assert {1, 6, 150} <= set(by_code)
    assert by_code[150]["label"] and by_code[150]["zh_tw"] == "授權 150"
    assert item["history_count"] >= 1 and item["history"]
    assert item["source_code"] is not None
    assert (
        report.sources.get(by_code[150]["label"]) == 1
    )  # 自訂來源有名稱（不是空白或數字）


def test_translated_junk_with_chinese_is_not_selected_by_default(tmp_path):
    db = _db(tmp_path)
    db.ingest(VERSION, [ScanItem("guide", "m", "g/a.md#1", LICENSE, zh_tw="授權條款")])
    db.ingest(VERSION, [ScanItem("lang", "m", "_comment", "Note", zh_tw="備註")])
    path = tmp_path / "p.json"
    report = db.export_protected_report(VERSION, path)
    items = {i["key"]: i for i in _read(path)["items"]}
    assert items["g/a.md#1"]["delete"] is False and items["g/a.md#1"]["review"]
    assert (
        items["_comment"]["delete"] is True
    )  # 語言檔備註靠鍵判斷，jar 繁中不算人工內容
    assert report.default_selected == 1 and report.review_required == 1
    assert report.exported_total == 2


def test_history_with_chinese_stops_a_default_delete(tmp_path):
    db = _db(tmp_path)
    db.ingest(VERSION, [ScanItem("lang", "m", "_comment", "Note", zh_tw="備註")])
    db.save_manual(_entry(db, "_comment"), "人工備註")
    path = tmp_path / "p.json"
    db.export_protected_report(VERSION, path)
    item = next(i for i in _read(path)["items"] if i["key"] == "_comment")
    assert item["delete"] is False  # 確定性類別，但有人工中文譯文與歷史


def test_everything_needing_review_still_counts_as_exported(tmp_path):
    db = _db(tmp_path)
    db.ingest(VERSION, [ScanItem("guide", "m", "g/a.md#1", LICENSE, zh_tw="授權條款")])
    report = db.export_protected_report(VERSION, tmp_path / "p.json")
    assert report.exported_total == 1 and report.default_selected == 0
    assert report.review_required == 1 and report.counts == {"license": 1}


# ---------------------------------------------------------------- B7 類別／C4 輸入
def test_changed_category_requires_a_new_export(tmp_path):
    db = _db(tmp_path)
    path = _rule_report(db, tmp_path)
    data = _read(path)
    data["items"][0]["category"] = "code_value"  # 類別對不上目前規則
    _write(path, data)
    plan = db.preview_cleanup(path)
    assert plan.skipped["清理類別已變動，請重新匯出"] == 1


@pytest.mark.parametrize("bad_id", [True, 1.0, -3, 0, "5", None], ids=lambda v: repr(v))
def test_ids_must_be_positive_integers(tmp_path, bad_id):
    db = _db(tmp_path)
    path = _rule_report(db, tmp_path)
    data = _read(path)
    data["items"] = [{**data["items"][0], "id": bad_id}]
    _write(path, data)
    plan = db.preview_cleanup(path)
    assert plan.deletable == 0 and plan.skipped["項目格式錯誤"] == 1


def test_duplicate_ids_are_counted_once(tmp_path):
    db = _db(tmp_path)
    path = _rule_report(db, tmp_path)
    data = _read(path)
    data["items"].append(dict(data["items"][0]))
    _write(path, data)
    plan = db.preview_cleanup(path)
    assert plan.skipped["重複的 id"] == 1
    assert plan.deletable == len(data["items"]) - 1


def test_has_translation_flag_in_a_rule_report_does_not_lift_protection(tmp_path):
    db = _db(tmp_path)
    db.ingest(VERSION, [ScanItem("guide", "m", "g/a.md#1", LICENSE, zh_tw="授權條款")])
    path = _rule_report(db, tmp_path)
    data = _read(path)
    entry_id = _entry(db, "g/a.md#1")
    data["items"].append(
        {
            "id": entry_id,
            "delete": True,
            "category": "license",
            "kind": "guide",
            "mod": "m",
            "key": "g/a.md#1",
            "en_us_hash": __import__(
                "translation_tool.translation_db.cleanup", fromlist=["_text_hash"]
            )._text_hash(LICENSE),
            "has_translation": True,
            "state_hash": db._translation_states([entry_id])[entry_id],
        }
    )
    _write(path, data)
    plan = db.preview_cleanup(path)
    assert entry_id not in plan.valid_ids
    assert plan.skipped["一般檢查檔不能標記已有譯文的項目"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"format": "mod-db-cleanup-v1", "version": VERSION, "items": []},
        {"format": "mod-db-cleanup-v2", "version": VERSION, "items": []},
        {
            "format": "mod-db-cleanup-v2",
            "mode": "other",
            "version": VERSION,
            "db_id": "x",
            "items": [],
        },
        {"format": "mod-db-cleanup-v2", "mode": "rule", "db_id": "x", "items": []},
        [],
    ],
)
def test_wrong_report_type_or_version_fails_safely(tmp_path, payload):
    db = _db(tmp_path)
    path = tmp_path / "bad.json"
    _write(path, payload)
    with pytest.raises(ValueError):
        db.preview_cleanup(path)


def test_broken_or_huge_report_fails_safely(tmp_path, monkeypatch):
    db = _db(tmp_path)
    broken = tmp_path / "broken.json"
    broken.write_text('{"format": "mod-db-', encoding="utf-8")
    with pytest.raises(ValueError):
        db.preview_cleanup(broken)
    from translation_tool.translation_db import cleanup

    monkeypatch.setattr(cleanup, "_MAX_REPORT_BYTES", 10)
    with pytest.raises(ValueError, match="過大"):
        db.preview_cleanup(_rule_report(db, tmp_path))


# ---------------------------------------------------------------- B5 原子寫入
def test_failed_write_leaves_the_existing_report_intact(tmp_path, monkeypatch):
    target = tmp_path / "r.json"
    _write_report(target, {"first": 1})

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        _write_report(target, {"second": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"first": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["r.json"]  # 沒留下暫存檔


def test_service_never_overwrites_an_earlier_export_from_the_same_second(
    tmp_path, monkeypatch
):
    from app.services_impl import moddb_cleanup_service as svc

    monkeypatch.setattr(svc.time, "strftime", lambda _fmt: "20260101-000000")
    db = _db(tmp_path)
    first = svc.export_cleanup(db, VERSION)
    first.path.write_text('{"user": "edited"}', encoding="utf-8")
    second = svc.export_cleanup(db, VERSION)
    assert second.path != first.path
    assert first.path.read_text(encoding="utf-8") == '{"user": "edited"}'
    assert len(svc.list_cleanup_reports(db)) == 2


def test_patchouli_lang_key_strings_are_cleaned_like_scanning_skips_them():
    from translation_tool.core.translatable_extractor import extract_translatables
    from translation_tool.translation_db.cleanup import entry_cleanup_category

    page = {"pages": [{"type": "text", "text": "book.mod.entry.page1"}]}
    assert (
        extract_translatables(
            page, Path("data/m/patchouli_books/b/en_us/entries/e.json")
        )
        == []
    )
    assert (
        entry_cleanup_category(
            "patchouli", "b/e.json#pages[0].text", "book.mod.entry.page1"
        )
        == "lang_key"
    )
    assert (
        entry_cleanup_category("patchouli", "b/e.json#pages[0].text", "Hello there")
        is None
    )


def test_empty_db_id_never_passes_the_cross_database_check(tmp_path):
    db = _db(tmp_path)
    path = _rule_report(db, tmp_path)
    data = _read(path)
    data["db_id"] = ""
    _write(path, data)
    other = _db(tmp_path, "other.db")  # 沒有 cleanup_db_id 的資料庫
    for target in (db, other):
        with pytest.raises(ValueError):
            target.preview_cleanup(path)
    fresh = _db(tmp_path, "fresh.db")
    data["db_id"] = "x"
    _write(path, data)
    with pytest.raises(ValueError, match="不是從目前的資料庫匯出"):
        fresh.preview_cleanup(path)  # 資料庫沒有身分也不能套用任何檢查檔


def test_advancement_cleanup_matches_what_the_scanner_collects():
    from translation_tool.core.text_sniffer import advancement_texts
    from translation_tool.translation_db.cleanup import entry_cleanup_category

    values = {
        "minecraft:stone": "not_text",
        "https://example.com/x": "not_text",
        "book.mod.entry.page": "lang_key",
        "Stone Age": None,
    }
    for value, expected in values.items():
        key = "a.json#display.title.text"
        assert entry_cleanup_category("advancement", key, value) == expected, value
        found = advancement_texts({"display": {"title": {"text": value}}})
        assert (not found) == (expected is not None), value


def test_src_change_added_after_the_preview_invalidates_the_plan(tmp_path):
    db = _db(tmp_path)
    path = _rule_report(db, tmp_path)
    plan = db.preview_cleanup(path)
    # 預覽後同一條目出現新的原文變更紀錄（沒有動到 entry 本身）
    db._conn.execute(
        "INSERT INTO src_change (kind, mc_version, mod_id, key, old_en, new_en) "
        "VALUES ('guide', ?, 'm', 'g/a.md#1', 'old', 'new')",
        (VERSION,),
    )
    db._conn.commit()
    before = _count(db)
    with pytest.raises(CleanupPlanChanged):
        db.apply_cleanup(path, plan.token)
    assert _count(db) == before


def test_partial_revert_rechecks_entries_it_already_restored(tmp_path):
    db = _db(tmp_path)
    plan = _apply(db, _rule_report(db, tmp_path))
    db.ingest(VERSION, [ScanItem("guide", "m", "g/a.md#1", LICENSE)])  # 其中一筆衝突
    first = db.revert_cleanup(plan.batch_id)
    assert not first.complete
    # 已還原的另一筆之後被刪除，原編號也被新資料用掉
    db._conn.execute("DELETE FROM entry WHERE key = 'g/a.md#0'")
    db._conn.execute("DELETE FROM entry WHERE key = 'g/a.md#1'")
    db._conn.commit()
    db.ingest(VERSION, [ScanItem("lang", "m", "item.m.new", "New")])
    second = db.revert_cleanup(plan.batch_id)
    assert second.complete
    keys = {r[0] for r in db._q("SELECT key FROM entry")}
    assert {"g/a.md#0", "g/a.md#1"} <= keys  # 沒有因為「記得還原過」而漏掉
