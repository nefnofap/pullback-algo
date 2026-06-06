"""
SQLite-backed dedupe store: same (ticker, side, bar_time) signal won't fire twice.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS fired_signals (
    ticker      TEXT NOT NULL,
    side        TEXT NOT NULL,
    bar_time    TEXT NOT NULL,
    fired_at    TEXT NOT NULL,
    entry_price REAL,
    sl          REAL,
    tp1         REAL,
    tp2         REAL,
    PRIMARY KEY (ticker, side, bar_time)
);
CREATE INDEX IF NOT EXISTS idx_fired_at ON fired_signals(fired_at);
"""


class State:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def already_fired(self, ticker: str, side: str, bar_time: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM fired_signals WHERE ticker=? AND side=? AND bar_time=?",
                (ticker, side, bar_time),
            ).fetchone()
            return row is not None

    def record(self, ticker: str, side: str, bar_time: str, fired_at: str,
               entry_price: float, sl: float, tp1: float, tp2: float) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO fired_signals "
                "(ticker, side, bar_time, fired_at, entry_price, sl, tp1, tp2) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, side, bar_time, fired_at, entry_price, sl, tp1, tp2),
            )

    def recent(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM fired_signals ORDER BY fired_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def prune_older_than(self, iso_cutoff: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM fired_signals WHERE fired_at < ?", (iso_cutoff,)
            )
            return cur.rowcount
