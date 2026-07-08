"""
Data ingestion: pulls OHLC price history from MT5 into pandas DataFrames.

Kept separate from indicator/signal logic so the rest of the bot never has
to know how bars were sourced (could later be swapped for a different feed
or a cached/replayed dataset for backtesting).
"""

import MetaTrader5 as mt5
import pandas as pd

import config
from logger_setup import get_logger

log = get_logger(__name__)

# Map friendly timeframe names (used in config.py) to MT5 constants.
_TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def resolve_timeframe(name=None):
    name = name or config.TIMEFRAME_NAME
    if name not in _TIMEFRAME_MAP:
        raise ValueError(f"Unknown timeframe '{name}', expected one of {list(_TIMEFRAME_MAP)}")
    return _TIMEFRAME_MAP[name]


def get_ohlc(symbol, timeframe_name=None, bars=None):
    """
    Fetch the most recent `bars` completed candles for `symbol` and return
    them as a DataFrame indexed by UTC timestamp, columns:
    open, high, low, close, tick_volume, spread, real_volume.

    Uses copy_rates_from_pos(..., start_pos=1, ...) — starting at position 1
    (not 0) so we only ever read *closed* bars, never the still-forming
    current candle, which would make indicator values shift as it develops.
    """
    timeframe = resolve_timeframe(timeframe_name)
    bars = bars or config.BARS_TO_FETCH

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"copy_rates_from_pos returned no data for {symbol}: {mt5.last_error()}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.set_index("time", inplace=True)
    return df
