"""
Rule-based entry signal generation.

Strategy: trend-filtered mean reversion, confirmed across two timeframes.
  - Only trade WITH the dominant trend (price vs EMA200 on H1, AND price vs
    EMA on the higher MTF_TIMEFRAME_NAME timeframe), fading short-term
    extremes rather than fighting the trend outright. This combination
    historically produces a higher win-rate than either pure mean-reversion
    (which fights strong trends) or pure breakout trading alone.
  - Long setup:  H1 trend bullish (close > EMA200) AND higher-timeframe
    trend also bullish AND close <= lower Bollinger Band AND RSI < oversold
    threshold.
  - Short setup: H1 trend bearish (close < EMA200) AND higher-timeframe
    trend also bearish AND close >= upper Bollinger Band AND RSI >
    overbought threshold.

No Martingale, no grid, no averaging down: this module only ever proposes a
single fresh entry based on current indicator values on the most recently
CLOSED candle — it has no concept of a losing streak or prior trades.

Three additional opt-in gates (all default OFF in config.py, see
SESSION_FILTER_ENABLED / VOLATILITY_FILTER_ENABLED / REGIME_ADAPTIVE_RSI_ENABLED)
can restrict entries to certain UTC hours, a normal ATR-percentile
volatility regime, and/or swap the RSI oversold/overbought thresholds based
on an ADX-classified market regime (market_regime.py) -- Trending accepts a
shallower pullback, Ranging requires a deeper extreme to avoid chop. All
applied before the strategy rules above.
"""

from collections import namedtuple

import pandas as pd

import config
import market_regime
import mtf_trend
from indicators import compute_all
from logger_setup import get_logger

log = get_logger(__name__)

Signal = namedtuple("Signal", ["direction", "entry_price", "atr", "reason"])
# direction: "buy" or "sell"


def generate(df, mtf_df):
    """
    Given an H1 OHLC DataFrame (as returned by data_feed.get_ohlc) and a
    higher-timeframe OHLC DataFrame (data_feed.get_ohlc with
    timeframe_name=config.MTF_TIMEFRAME_NAME), evaluate the entry rules
    against the last CLOSED H1 bar plus the higher timeframe's current
    trend.

    Returns a Signal namedtuple, or None if no setup is present.
    """
    enriched = compute_all(df, config)

    # Need enough history for the slowest indicator (EMA200) to be valid.
    last = enriched.iloc[-1]
    if last[["rsi", "ema_trend", "bb_upper", "bb_lower", "atr"]].isna().any():
        log.debug("Indicators not fully warmed up yet, skipping signal check")
        return None

    higher_tf_trend = mtf_trend.latest_trend(mtf_df, config.MTF_EMA_PERIOD)
    if higher_tf_trend is None:
        log.debug("Higher-timeframe (%s) trend not warmed up yet, skipping signal check", config.MTF_TIMEFRAME_NAME)
        return None

    if config.SESSION_FILTER_ENABLED and last.name.hour not in config.SESSION_ALLOWED_HOURS_UTC:
        log.debug("Session filter blocked entry: bar hour %d UTC not in allowed hours", last.name.hour)
        return None

    if config.VOLATILITY_FILTER_ENABLED:
        pctl = last["atr_percentile"]
        if pd.isna(pctl) or not (config.VOLATILITY_MIN_PERCENTILE <= pctl <= config.VOLATILITY_MAX_PERCENTILE):
            log.debug("Volatility regime filter blocked entry: ATR percentile %s outside allowed range", pctl)
            return None

    rsi_oversold, rsi_overbought = config.RSI_OVERSOLD, config.RSI_OVERBOUGHT
    regime = None
    if config.REGIME_ADAPTIVE_RSI_ENABLED:
        regime = market_regime.classify(last["adx"], config.ADX_TRENDING_THRESHOLD)
        if regime is None:
            log.debug("ADX not fully warmed up yet, skipping signal check")
            return None
        if regime == market_regime.TRENDING:
            rsi_oversold, rsi_overbought = config.RSI_OVERSOLD_TRENDING, config.RSI_OVERBOUGHT_TRENDING
        else:
            rsi_oversold, rsi_overbought = config.RSI_OVERSOLD_RANGING, config.RSI_OVERBOUGHT_RANGING

    close = last["close"]

    bullish_trend = close > last["ema_trend"] and higher_tf_trend == "bullish"
    bearish_trend = close < last["ema_trend"] and higher_tf_trend == "bearish"

    long_setup = (
        bullish_trend
        and close <= last["bb_lower"]
        and last["rsi"] < rsi_oversold
    )
    short_setup = (
        bearish_trend
        and close >= last["bb_upper"]
        and last["rsi"] > rsi_overbought
    )

    regime_note = f", regime={regime}" if regime is not None else ""

    if long_setup:
        return Signal(
            direction="buy",
            entry_price=close,
            atr=last["atr"],
            reason=(
                f"close {close:.5f} <= lower BB {last['bb_lower']:.5f}, "
                f"RSI {last['rsi']:.1f} < {rsi_oversold}, "
                f"trend bullish (close > EMA{config.TREND_FILTER_EMA_PERIOD} "
                f"and {config.MTF_TIMEFRAME_NAME} trend bullish){regime_note}"
            ),
        )

    if short_setup:
        return Signal(
            direction="sell",
            entry_price=close,
            atr=last["atr"],
            reason=(
                f"close {close:.5f} >= upper BB {last['bb_upper']:.5f}, "
                f"RSI {last['rsi']:.1f} > {rsi_overbought}, "
                f"trend bearish (close < EMA{config.TREND_FILTER_EMA_PERIOD} "
                f"and {config.MTF_TIMEFRAME_NAME} trend bearish){regime_note}"
            ),
        )

    return None
