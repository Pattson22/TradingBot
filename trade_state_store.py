"""
Persistence for trade_manager's exit-tier state.

A tiny sqlite3 wrapper (stdlib only, no new dependency) so that break-even /
partial-TP / trailing-stop progress survives a bot restart instead of living
only in a process-local dict. trade_manager.py is still the source of truth
for state *shape* and mutation logic — this module only knows how to shuffle
that shape to and from disk.
"""

import os
import sqlite3

import config

os.makedirs(config.DATA_DIR, exist_ok=True)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_state (
    ticket INTEGER PRIMARY KEY,
    initial_stop_distance REAL NOT NULL,
    be_applied INTEGER NOT NULL,
    partial_done INTEGER NOT NULL,
    extreme_price REAL NOT NULL
)
"""


def _connect():
    conn = sqlite3.connect(config.STATE_DB_PATH)
    conn.execute(_SCHEMA)
    return conn


def load_all():
    """Return {ticket: state_dict} for every persisted trade."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT ticket, initial_stop_distance, be_applied, partial_done, "
            "extreme_price FROM trade_state"
        ).fetchall()
    finally:
        conn.close()
    return {
        ticket: {
            "initial_stop_distance": initial_stop_distance,
            "be_applied": bool(be_applied),
            "partial_done": bool(partial_done),
            "extreme_price": extreme_price,
        }
        for ticket, initial_stop_distance, be_applied, partial_done, extreme_price in rows
    }


def save(ticket, state):
    """Insert or overwrite the persisted state for a single ticket."""
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO trade_state (ticket, initial_stop_distance, be_applied, "
                "partial_done, extreme_price) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(ticket) DO UPDATE SET "
                "initial_stop_distance=excluded.initial_stop_distance, "
                "be_applied=excluded.be_applied, "
                "partial_done=excluded.partial_done, "
                "extreme_price=excluded.extreme_price",
                (
                    ticket,
                    state["initial_stop_distance"],
                    int(state["be_applied"]),
                    int(state["partial_done"]),
                    state["extreme_price"],
                ),
            )
    finally:
        conn.close()


def delete_many(tickets):
    """Remove persisted state for tickets that are no longer open positions."""
    if not tickets:
        return
    conn = _connect()
    try:
        with conn:
            conn.executemany(
                "DELETE FROM trade_state WHERE ticket = ?", [(t,) for t in tickets]
            )
    finally:
        conn.close()
