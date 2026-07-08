"""
Historical OHLC data for backtesting.

Reuses the live MT5 connection (broker.py must already be connected) to pull
real historical bars via mt5.copy_rates_range, then caches them to a local
CSV keyed by symbol/timeframe/date-range so repeat backtest runs over the
same window don't need to re-fetch from the broker.
"""

import os

import MetaTrader5 as mt5
import pandas as pd

import config
from data_feed import resolve_timeframe
from logger_setup import get_logger

log = get_logger(__name__)

CACHE_DIR = "backtest_data"


def _cache_path(symbol, timeframe_name, start, end):
    fname = f"{symbol}_{timeframe_name}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    return os.path.join(CACHE_DIR, fname)


def fetch_historical_bars(symbol, start, end, timeframe_name=None, use_cache=True):
    """
    Return a DataFrame of historical OHLC bars for `symbol` between `start`
    and `end` (both timezone-aware UTC datetimes), indexed by UTC timestamp
    with the same columns as data_feed.get_ohlc. Requires an active MT5
    connection (call broker.connect() first) unless a cached file already
    covers this exact request.
    """
    timeframe_name = timeframe_name or config.TIMEFRAME_NAME
    path = _cache_path(symbol, timeframe_name, start, end)

    if use_cache and os.path.exists(path):
        log.info("Loading cached historical bars from %s", path)
        return pd.read_csv(path, parse_dates=["time"], index_col="time")

    timeframe = resolve_timeframe(timeframe_name)
    rates = mt5.copy_rates_range(symbol, timeframe, start, end)
    if rates is None or len(rates) == 0:
        raise RuntimeError(
            f"copy_rates_range returned no data for {symbol} "
            f"[{start} - {end}]: {mt5.last_error()}"
        )

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.set_index("time", inplace=True)

    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_csv(path)
    log.info("Fetched %d bars for %s [%s - %s], cached to %s", len(df), symbol, start, end, path)
    return df
