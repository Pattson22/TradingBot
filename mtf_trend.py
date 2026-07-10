"""
Multi-timeframe trend confirmation, shared by the live signal generator
(signals.py) and the backtest engine (backtest_engine.py).

Entries require a higher timeframe's own EMA trend to AGREE WITH the
existing H1 EMA200 trend filter, not replace it — see config.py's MTF_*
settings and signals.py/backtest_engine.py for how the two are combined.
"""

import pandas as pd

import indicators

# Bar duration in hours, used only by align_series() to know how long a
# higher-timeframe bar takes to actually close.
_TIMEFRAME_HOURS = {
    "M1": 1 / 60,
    "M5": 5 / 60,
    "M15": 15 / 60,
    "M30": 30 / 60,
    "H1": 1,
    "H4": 4,
    "D1": 24,
}


def _direction(close, ema):
    if pd.isna(ema):
        return None
    if close > ema:
        return "bullish"
    if close < ema:
        return "bearish"
    return None


def latest_trend(mtf_df, ema_period):
    """
    Trend direction ('bullish'/'bearish'/None) from the most recent CLOSED
    bar of a higher-timeframe OHLC DataFrame (as returned by
    data_feed.get_ohlc, which already excludes the still-forming bar).
    None if the EMA hasn't warmed up yet. Used by the live bot.
    """
    if len(mtf_df) == 0:
        return None
    ema = indicators.ema(mtf_df["close"], ema_period)
    return _direction(mtf_df["close"].iloc[-1], ema.iloc[-1])


def align_series(h1_index, mtf_df, ema_period, mtf_timeframe_name):
    """
    For backtesting: return a pandas Series indexed like `h1_index`, giving
    each H1 bar's higher-timeframe trend direction ('bullish'/'bearish'/
    None) using only higher-timeframe bars that had actually CLOSED by that
    H1 bar's own timestamp.

    MT5 bar timestamps mark a bar's OPEN, not its close, so a higher-
    timeframe bar's close/EMA aren't knowable until `mtf_timeframe_name`'s
    duration after its own timestamp. Shifting the higher-timeframe index
    forward by that duration before an as-of (backward) join is what
    prevents a not-yet-closed higher-timeframe bar from leaking into an
    earlier H1 bar's signal check — without this shift, an H1 bar would see
    "the future" of the H4 bar still forming around it.
    """
    hours = _TIMEFRAME_HOURS[mtf_timeframe_name]
    ema = indicators.ema(mtf_df["close"], ema_period)

    mtf_trend_df = pd.DataFrame({"mtf_close": mtf_df["close"], "mtf_ema": ema})
    mtf_trend_df.index = mtf_trend_df.index + pd.Timedelta(hours=hours)
    mtf_trend_df.index.name = "time"
    mtf_trend_df = mtf_trend_df.sort_index()

    h1_frame = pd.DataFrame(index=h1_index)
    h1_frame.index.name = "time"

    # merge_asof requires both "time" columns to share the exact same
    # datetime64 resolution. A fresh MT5 fetch (unit="s") and this
    # function's own index + pd.Timedelta arithmetic above can end up at
    # different resolutions (e.g. datetime64[s] vs [us]) depending on the
    # pandas version, even though both represent UTC instants -- normalize
    # both explicitly rather than relying on them already matching.
    h1_reset = h1_frame.reset_index().sort_values("time")
    mtf_reset = mtf_trend_df.reset_index()
    h1_reset["time"] = h1_reset["time"].astype("datetime64[ns, UTC]")
    mtf_reset["time"] = mtf_reset["time"].astype("datetime64[ns, UTC]")

    merged = pd.merge_asof(
        h1_reset,
        mtf_reset,
        on="time",
        direction="backward",
    ).set_index("time")

    direction = pd.Series(None, index=merged.index, dtype=object)
    direction[merged["mtf_close"] > merged["mtf_ema"]] = "bullish"
    direction[merged["mtf_close"] < merged["mtf_ema"]] = "bearish"
    return direction.reindex(h1_index)
