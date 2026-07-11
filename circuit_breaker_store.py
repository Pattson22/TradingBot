"""
Persistence for circuit_breaker's daily drawdown-tracking state.

This bot restarts often (laptop reboots, crashes) -- without persistence, a
restart mid-day would silently reset the day's starting equity or clear an
active halt, letting the bot bypass MAX_DAILY_DRAWDOWN_PCT for the rest of
the day. Single-row table: the bot only ever tracks one "today" at a time.
"""

import os
import sqlite3

import config

os.makedirs(config.DATA_DIR, exist_ok=True)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    day TEXT NOT NULL,
    day_start_equity REAL NOT NULL,
    halted INTEGER NOT NULL
)
"""


def _connect():
    conn = sqlite3.connect(config.STATE_DB_PATH)
    conn.execute(_SCHEMA)
    return conn


def load():
    """Return {"day": "YYYY-MM-DD", "day_start_equity": float, "halted": bool},
    or None if nothing has been persisted yet."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT day, day_start_equity, halted FROM circuit_breaker_state WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    day, day_start_equity, halted = row
    return {"day": day, "day_start_equity": day_start_equity, "halted": bool(halted)}


def save(day, day_start_equity, halted):
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO circuit_breaker_state (id, day, day_start_equity, halted) "
                "VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "day=excluded.day, day_start_equity=excluded.day_start_equity, halted=excluded.halted",
                (day, day_start_equity, int(halted)),
            )
    finally:
        conn.close()
