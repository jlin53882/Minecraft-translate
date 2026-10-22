"""tests/test_sqlite_wal_recovery.py
用途：驗證 CacheSearchEngine 的 SQLite WAL 當機復原能力。
模擬 unclean shutdown（WAL 未 checkpoint），確認重新開啟 DB 時資料仍完整。
"""
import sqlite3
import tempfile
from pathlib import Path


def test_sqlite_wal_unclean_shutdown_recovery():
    """模擬 unclean shutdown（WAL 未 checkpoint），確認 SQLite DB 可正常重新開啟。

    情境：
    1. 以 WAL mode 建立 DB，寫入資料
    2. 直接刪除 WAL / SHM 檔（模擬當機崩潰）
    3. 重新連線，確認資料未損壞且可正常讀取
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_wal.db"

        # --- Arrange: 建立 WAL mode DB 並寫入資料 ---
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("CREATE TABLE test(id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO test VALUES (1, 'hello')")
        conn.execute("INSERT INTO test VALUES (2, 'world')")
        conn.commit()
        conn.close()

        # --- Act: 刪除 WAL / SHM（模擬 crash） ---
        for suffix in ["-wal", "-shm"]:
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()

        # --- Assert: 重新開啟應正常，資料完整 ---
        conn2 = sqlite3.connect(str(db_path))
        rows = conn2.execute("SELECT * FROM test ORDER BY id").fetchall()
        conn2.close()

        assert rows == [(1, "hello"), (2, "world")], (
            f"WAL crash recovery 失敗，期望 [(1,'hello'), (2,'world')]，得到 {rows}"
        )
