"""
Rule-based entry signal generation.

Strategy: trend-filtered mean reversion.
  - Only trade WITH the dominant trend (price vs EMA200), fading short-term
    extremes rather than fighting the trend outright. This combination
    historically produces a higher win-rate than either pure mean-reversion
    (which fights strong trends) or pure breakout trading alone.
  - Long setup:  trend is bullish (close > EMA200) AND close <= lower
    Bollinger Band AND RSI < oversold threshold.
  - Short setup: trend is bearish (close < EMA200) AND close >= upper
    Bollinger Band AND RSI > overbought threshold.

No Martingale, no grid, no averaging down: this module only ever proposes a
single fresh entry based on current indicator values on the most recently
CLOSED candle — it has no concept of a losing streak or prior trades.
"""

from collections import namedtuple

import config
from indicators import compute_all
from logger_setup import get_logger

log = get_logger(__name__)

Signal = namedtuple("Signal", ["direction", "entry_price", "atr", "reason"])
# direction: "buy" or "sell"


def generate(df):
    """
    Given an OHLC DataFrame (as returned by data_feed.get_ohlc), compute
    indicators and evaluate the entry rules against the last CLOSED bar.

    Returns a Signal namedtuple, or None if no setup is present.
    """
    enriched = compute_all(df, config)

    # Need enough history for the slowest indicator (EMA200) to be valid.
    last = enriched.iloc[-1]
    if last[["rsi", "ema_trend", "bb_upper", "bb_lower", "atr"]].isna().any():
        log.debug("Indicators not fully warmed up yet, skipping signal check")
        return None

    close = last["close"]

    bullish_trend = close > last["ema_trend"]
    bearish_trend = close < last["ema_trend"]

    long_setup = (
        bullish_trend
        and close <= last["bb_lower"]
        and last["rsi"] < config.RSI_OVERSOLD
    )
    short_setup = (
        bearish_trend
        and close >= last["bb_upper"]
        and last["rsi"] > config.RSI_OVERBOUGHT
    )

    if long_setup:
        return Signal(
            direction="buy",
            entry_price=close,
            atr=last["atr"],
            reason=(
                f"close {close:.5f} <= lower BB {last['bb_lower']:.5f}, "
                f"RSI {last['rsi']:.1f} < {config.RSI_OVERSOLD}, "
                f"trend bullish (close > EMA{config.TREND_FILTER_EMA_PERIOD})"
            ),
        )

    if short_setup:
        return Signal(
            direction="sell",
            entry_price=close,
            atr=last["atr"],
            reason=(
                f"close {close:.5f} >= upper BB {last['bb_upper']:.5f}, "
                f"RSI {last['rsi']:.1f} > {config.RSI_OVERBOUGHT}, "
                f"trend bearish (close < EMA{config.TREND_FILTER_EMA_PERIOD})"
            ),
        )

    return None
