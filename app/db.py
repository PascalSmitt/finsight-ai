"""SQLite persistence layer (standard library only)."""
from __future__ import annotations

import csv
import os
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.environ.get("FINSIGHT_DB", os.path.join(DATA_DIR, "finsight.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS financials (
    ticker              TEXT    NOT NULL,
    company             TEXT    NOT NULL,
    fiscal_year         INTEGER NOT NULL,
    fiscal_year_end     TEXT    NOT NULL,
    revenue             REAL,
    net_income          REAL,
    total_assets        REAL,
    total_liabilities   REAL,
    operating_cash_flow REAL,
    PRIMARY KEY (ticker, fiscal_year)
);
CREATE TABLE IF NOT EXISTS chat_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('user', 'bot')),
    text       TEXT NOT NULL,
    intent     TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_logs (session_id, id);
"""
NUMERIC = ["revenue", "net_income", "total_assets", "total_liabilities", "operating_cash_flow"]


@contextmanager
def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


def load_csv(path: str, replace: bool = False) -> int:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    with connect() as c:
        if replace:
            c.execute("DELETE FROM financials")
        c.executemany(
            "INSERT OR REPLACE INTO financials (ticker, company, fiscal_year, fiscal_year_end, "
            "revenue, net_income, total_assets, total_liabilities, operating_cash_flow) "
            "VALUES (:ticker, :company, :fiscal_year, :fiscal_year_end, :revenue, :net_income, "
            ":total_assets, :total_liabilities, :operating_cash_flow)",
            rows,
        )
    return len(rows)


def seed_if_empty(csv_path: str) -> None:
    init_db()
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM financials").fetchone()[0]
    if n == 0 and os.path.exists(csv_path):
        load_csv(csv_path)


def fetch_financials() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM financials ORDER BY ticker, fiscal_year")]


def log_message(session_id: str, role: str, text: str, intent: str | None = None) -> None:
    with connect() as c:
        c.execute("INSERT INTO chat_logs (session_id, role, text, intent) VALUES (?, ?, ?, ?)",
                  (session_id, role, text, intent))


def chat_history(session_id: str, limit: int = 50) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT role, text, intent, created_at FROM chat_logs "
                         "WHERE session_id = ? ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]
