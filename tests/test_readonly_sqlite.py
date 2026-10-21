"""End-to-end check that run_query cannot write, even if the validator were bypassed."""
import asyncio
import sqlite3

from src.db.drivers.sqlite import SQLiteDriver


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO users (name) VALUES ('a'), ('b')")
    conn.commit()
    conn.close()


def test_run_query_reads(tmp_path):
    db = tmp_path / "t.db"
    _make_db(db)
    driver = SQLiteDriver()
    asyncio.run(driver.connect(f"sqlite:///{db}"))
    result = asyncio.run(driver.run_query("SELECT name FROM users ORDER BY id"))
    assert result["isError"] is False
    assert '"row_count": 2' in result["content"]


def test_readonly_connection_rejects_writes_past_the_validator(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    _make_db(db)
    driver = SQLiteDriver()
    asyncio.run(driver.connect(f"sqlite:///{db}"))
    # Simulate a validator bypass: the read-only connection must still refuse.
    monkeypatch.setattr(driver, "validate_query", lambda sql: (True, None))
    monkeypatch.setattr(driver, "inject_limit", lambda sql, limit: sql)
    result = asyncio.run(driver.run_query("DELETE FROM users"))
    assert result["isError"] is True
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    conn.close()
