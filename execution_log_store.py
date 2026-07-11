"""
Persistence for order-execution tracking: requested vs. filled price for
every live entry order, so slippage (executed_price - requested_price) can
be reviewed after the fact instead of only appearing transiently in the
log. Same sqlite-per-row pattern as trade_state_store.py /
circuit_breaker_store.py, same database file.
"""

import os
import sqlite3
import time
from typing import Optional

import config

os.makedirs(config.DATA_DIR, exist_ok=True)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket INTEGER,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    requested_price REAL NOT NULL,
    executed_price REAL NOT NULL,
    slippage REAL NOT NULL,
    timestamp REAL NOT NULL
)
"""


def _connect():
    conn = sqlite3.connect(config.STATE_DB_PATH)
    conn.execute(_SCHEMA)
    return conn


def record(
    ticket: Optional[int],
    symbol: str,
    direction: str,
    requested_price: float,
    executed_price: float,
) -> float:
    """Persist one fill's requested-vs-executed price and return the signed
    slippage (executed_price - requested_price, in price terms -- positive
    means the fill was worse than requested for a buy, better for a sell;
    callers should account for `direction` if they need a signed-by-cost
    interpretation)."""
    slippage = executed_price - requested_price
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO execution_log (ticket, symbol, direction, requested_price, "
                "executed_price, slippage, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticket, symbol, direction, requested_price, executed_price, slippage, time.time()),
            )
    finally:
        conn.close()
    return slippage


def recent(limit: int = 50):
    """Return the most recent `limit` execution records, newest first."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT ticket, symbol, direction, requested_price, executed_price, slippage, timestamp "
            "FROM execution_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "ticket": ticket, "symbol": symbol, "direction": direction,
            "requested_price": requested_price, "executed_price": executed_price,
            "slippage": slippage, "timestamp": timestamp,
        }
        for ticket, symbol, direction, requested_price, executed_price, slippage, timestamp in rows
    ]
