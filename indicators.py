"""
Technical indicator calculations, implemented directly on pandas/NumPy
(no third-party TA library) so the math is transparent and dependency-light.

Every function takes a DataFrame with at least an OHLC 'close' column (and
'high'/'low' for ATR) and returns a pandas Series aligned to the same index.
"""

import numpy as np
import pandas as pd


def sma(series, period):
    return series.rolling(window=period, min_periods=period).mean()


def ema(series, period):
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close, period=14):
    """
    Wilder's RSI. Uses an exponential (Wilder) moving average of gains and
    losses rather than a simple average, which is the standard definition
    and avoids RSI being overly jumpy on the earliest bars.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder smoothing == EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    # Where avg_loss is 0 (no losses in window) RSI is 100 by definition.
    result = result.where(avg_loss != 0, 100)
    return result


def bollinger_bands(close, period=20, std_dev=2.0):
    """Return (middle, upper, lower) bands as a tuple of Series."""
    middle = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return middle, upper, lower


def atr(df, period=14):
    """
    Average True Range using Wilder smoothing, computed from a DataFrame
    with 'high', 'low', 'close' columns.
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def adx(df, period=14):
    """
    Wilder's Average Directional Index -- measures trend STRENGTH, not
    direction, used by market_regime.py to classify Trending
    (ADX > config.ADX_TRENDING_THRESHOLD) vs Ranging markets. Needs roughly
    2x `period` bars to fully warm up: the +DI/-DI smoothing needs `period`
    bars, then ADX itself is a second Wilder smoothing of DX on top of that
    -- a real property of ADX, not a bug.
    """
    high, low = df["high"], df["low"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    smoothed_tr = atr(df, period)
    smoothed_plus_dm = plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    smoothed_minus_dm = minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    plus_di = 100 * smoothed_plus_dm / smoothed_tr
    minus_di = 100 * smoothed_minus_dm / smoothed_tr

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum

    return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def atr_percentile_rank(atr_series, lookback=100):
    """
    Rolling percentile rank (0-1) of each bar's ATR value within its own
    trailing `lookback` bars -- e.g. 0.9 means "ATR is higher than 90% of
    the last `lookback` readings". Used to gate entries by volatility
    regime (too quiet -> chop with no follow-through; too wild -> news-spike
    risk), see config.VOLATILITY_FILTER_ENABLED.
    """
    def _rank(window):
        return (window <= window[-1]).mean()

    return atr_series.rolling(window=lookback, min_periods=lookback).apply(_rank, raw=True)


def compute_all(df, config):
    """
    Convenience helper: attach every indicator the strategy needs as new
    columns on a copy of `df`, using periods from the config module.
    """
    out = df.copy()
    out["rsi"] = rsi(out["close"], config.RSI_PERIOD)
    out["ema_trend"] = ema(out["close"], config.TREND_FILTER_EMA_PERIOD)
    _, out["bb_upper"], out["bb_lower"] = bollinger_bands(
        out["close"], config.BOLLINGER_PERIOD, config.BOLLINGER_STD_DEV
    )
    out["atr"] = atr(out, config.ATR_PERIOD)
    out["atr_percentile"] = atr_percentile_rank(out["atr"], config.VOLATILITY_PERCENTILE_LOOKBACK)
    out["adx"] = adx(out, config.ADX_PERIOD)
    return out
