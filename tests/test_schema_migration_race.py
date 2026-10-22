"""兩個程序同時開啟舊版資料庫：版本必須在鎖內重讀，已執行的 migration 不可重跑。"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tests.test_translation_db_core import _make_v1_db
from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.schema import SCHEMA_VERSION, SRC_MANUAL

REPO_ROOT = Path(__file__).resolve().parents[1]

# 程序 B：init_schema 第一次讀 schema 版本之後、取得寫鎖之前停住，等父程序（A）完成整個升級才繼續。
_PROCESS_B = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from translation_tool.translation_db import TranslationDB, schema

    db_path, read_flag, go_flag = sys.argv[1:4]
    real = schema.stored_schema_version
    state = {"first": True}

    def paused_version(conn):
        version = real(conn)
        if state["first"] and sys._getframe(1).f_code.co_name == "init_schema":
            state["first"] = False
            Path(read_flag).write_text(str(version))
            deadline = time.time() + 60
            while not Path(go_flag).exists() and time.time() < deadline:
                time.sleep(0.02)
        return version

    schema.stored_schema_version = paused_version
    TranslationDB(db_path).close()
    """
)


def _spawn_b(path: Path, read_flag: Path, go_flag: Path) -> subprocess.Popen:
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    return subprocess.Popen(
        [sys.executable, "-c", _PROCESS_B, str(path), str(read_flag), str(go_flag)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for(flag: Path, proc: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while not flag.exists():
        if proc.poll() is not None:
            raise AssertionError(f"程序 B 提前結束：{proc.communicate()}")
        if time.time() > deadline:
            proc.kill()
            raise AssertionError("程序 B 沒有在時限內讀到版本")
        time.sleep(0.02)


def test_second_opener_rereads_version_under_lock_and_skips_done_migrations(tmp_path):
    path = tmp_path / "race.db"
    entry_id = _make_v1_db(path)
    read_flag, go_flag = tmp_path / "b_read", tmp_path / "go"

    proc = _spawn_b(path, read_flag, go_flag)
    try:
        # B 已依「版本 1」決定要 migrate，但還沒取得寫鎖。
        _wait_for(read_flag, proc)
        assert read_flag.read_text() == "1"

        # A 完成整個升級，並由人工把這筆譯文審核為 reviewed。
        a = TranslationDB(path)
        a._conn.execute(
            "UPDATE translation SET review_status='reviewed', checker='human' "
            "WHERE entry_id=? AND source=?",
            (entry_id, SRC_MANUAL),
        )
        a._conn.execute(
            "UPDATE effective SET review_status='reviewed' WHERE entry_id=?",
            (entry_id,),
        )
        a._conn.commit()
        a.close()
        backups_after_a = sorted(p.name for p in tmp_path.glob("race.db.pre-schema-v*"))

        go_flag.touch()
        _out, err = proc.communicate(timeout=120)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, err
    assert "database is locked" not in err

    with sqlite3.connect(path) as conn:
        version = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        status = conn.execute(
            "SELECT review_status FROM translation WHERE entry_id=? AND source=?",
            (entry_id, SRC_MANUAL),
        ).fetchone()[0]
        effective = conn.execute(
            "SELECT review_status FROM effective WHERE entry_id=?", (entry_id,)
        ).fetchone()[0]
    assert version == str(SCHEMA_VERSION)
    # 第二個開啟者不可重跑 v2 回填，把人工審核蓋回 legacy_unknown
    assert status == "reviewed" and effective == "reviewed"
    # 每個版本只備份一次，B 不會再多建一份（或覆蓋）備份
    assert sorted(p.name for p in tmp_path.glob("race.db.pre-schema-v*")) == (
        backups_after_a
    )
    assert len(backups_after_a) == SCHEMA_VERSION - 1


def test_migration_retries_after_a_crashed_attempt(tmp_path, monkeypatch):
    """migration 崩潰會整段 rollback，下一次開啟可重新升級。"""
    from translation_tool.translation_db import schema

    path = tmp_path / "retry.db"
    _make_v1_db(path)
    real = schema._migrate_time_and_revision_v3
    calls = {"n": 0}

    def crash_once(conn):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash")
        real(conn)

    monkeypatch.setattr(schema, "_migrate_time_and_revision_v3", crash_once)
    with pytest.raises(RuntimeError, match="simulated crash"):
        TranslationDB(path)
    with sqlite3.connect(path) as conn:
        assert (
            conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()[0]
            == "2"
        )
    db = TranslationDB(path)
    db.close()
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0] == str(SCHEMA_VERSION)
