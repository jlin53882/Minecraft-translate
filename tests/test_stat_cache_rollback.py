"""持久化統計快取（stat_cache）寫入失敗：必須回復本次寫入、不持有寫鎖、仍回傳計算結果。"""

from __future__ import annotations

import sqlite3

import pytest

from translation_tool.translation_db import TranslationDB


class FaultyConn:
    """包住真正的連線，在指定 SQL（或 commit）時丟出 sqlite3.Error。"""

    def __init__(self, real, *, fail_on: str):
        self._real = real
        self._fail_on = fail_on

    def execute(self, sql, *args):
        if self._fail_on != "commit" and sql.lstrip().startswith(self._fail_on):
            raise sqlite3.OperationalError("simulated cache write failure")
        return self._real.execute(sql, *args)

    def commit(self):
        if self._fail_on == "commit":
            raise sqlite3.OperationalError("simulated commit failure")
        return self._real.commit()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _gen_with_old_cache(db: TranslationDB) -> None:
    """先放一筆舊世代快取，讓 DELETE 真的有東西可刪。"""
    db._conn.execute(
        "INSERT OR REPLACE INTO stat_cache (key, gen, value) VALUES ('old', 'stale', '1')"
    )
    db._conn.commit()


@pytest.mark.parametrize("fail_on", ["INSERT OR REPLACE INTO stat_cache", "commit"])
def test_cache_write_failure_rolls_back_and_releases_write_lock(tmp_path, fail_on):
    path = tmp_path / "cache.db"
    db = TranslationDB(path)
    _gen_with_old_cache(db)
    real = db._conn
    db._conn = FaultyConn(real, fail_on=fail_on)

    # 快取失敗不影響計算結果
    assert db._cached("k", lambda: {"n": 3}) == {"n": 3}

    db._conn = real
    assert real.in_transaction is False
    # 其他連線可立刻取得寫鎖（沒有被遺留的交易卡住）
    other = sqlite3.connect(path, timeout=0.2)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.rollback()
    finally:
        other.close()
    # 本次快取寫入被回復：舊世代列仍在，沒有半套的 DELETE
    assert real.execute(
        "SELECT COUNT(*) FROM stat_cache WHERE key='old'"
    ).fetchone() == (1,)
    # 後續正常寫入與統計不受影響
    assert db._cached("k2", lambda: [1, 2]) == [1, 2]
    db.ingest("1.21.1", [])
    db.close()


def test_cache_write_does_not_commit_a_callers_pending_transaction(tmp_path):
    """呼叫端已有未提交的交易時：不得替它 commit，也不得 rollback 它。"""
    db = TranslationDB(tmp_path / "owner.db")
    real = db._conn
    real.execute("INSERT INTO meta (key, value) VALUES ('caller_pending', '1')")
    assert real.in_transaction is True

    assert db._cached("k", lambda: 7) == 7

    assert real.in_transaction is True  # 呼叫端的交易仍在
    real.rollback()
    assert (
        real.execute("SELECT 1 FROM meta WHERE key='caller_pending'").fetchone() is None
    )
    db.close()


def test_cache_write_does_not_wait_for_another_connections_write_lock(tmp_path):
    """快取寫入遇到別的連線持有寫入鎖時立刻放棄（不等數秒的 busy timeout），並恢復原本的等待時間。"""
    path = tmp_path / "cache.db"
    db = TranslationDB(path)
    other = sqlite3.connect(path)
    other.execute("BEGIN IMMEDIATE")  # 模擬掃描／批次寫入持有寫入鎖
    waits: list[int] = []
    real = db._conn

    class Spy:
        def execute(self, sql, *args):
            cursor = real.execute(sql, *args)
            if sql == "PRAGMA busy_timeout":
                waits.append(("read", cursor.fetchone()[0]))
                return real.execute(sql, *args)
            if sql.startswith("PRAGMA busy_timeout ="):
                waits.append(("set", int(sql.rsplit("=", 1)[1])))
            return cursor

        def __getattr__(self, name):
            return getattr(real, name)

    db._conn = Spy()
    try:
        assert db._cached("k", lambda: {"n": 3}) == {"n": 3}
    finally:
        other.rollback()
        other.close()
    db._conn = real

    assert waits[0] == ("read", 5000)
    assert ("set", 0) in waits  # 寫入期間不等鎖
    assert waits[-1] == ("set", 5000)  # 之後恢復
    assert db._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert db._one("SELECT COUNT(*) FROM stat_cache WHERE key = 'k'")[0] == 0
