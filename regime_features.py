"""
Feature engineering for the ML market-regime classifier (ml_regime_classifier.py).

Takes an already-enriched OHLC DataFrame (indicators.compute_all's output --
must have close/ema_trend/rsi/atr/atr_percentile/adx columns) and derives the
feature matrix used for BOTH training and live/backtest prediction, so the
two paths can never silently diverge in how a feature is computed.

Two features are new here, not already columns from indicators.compute_all:
  - bb_width: Bollinger band width normalized by price -- a volatility/
    squeeze proxy independent of ATR's own smoothing.
  - ema_slope: rate of change of the trend EMA, normalized by ATR so it's
    comparable across volatility regimes -- how fast the trend line itself
    is moving, which ADX (a strength measure) doesn't directly capture.
  - close_to_ema_atr: how far price has stretched from the trend EMA,
    normalized by ATR.
"""

from __future__ import annotations

import pandas as pd

FEATURE_COLUMNS = ["adx", "rsi", "atr_percentile", "bb_width", "ema_slope", "close_to_ema_atr"]


def build_features(enriched_df: pd.DataFrame, ema_slope_lookback: int = 5) -> pd.DataFrame:
    """Return a new DataFrame with exactly FEATURE_COLUMNS, index-aligned
    with `enriched_df`. Does not mutate the input. Rows still inside any
    underlying indicator's warm-up period come out as NaN, same as
    indicators.compute_all's own columns -- callers are responsible for
    dropping/handling those (see ml_regime_classifier.py)."""
    atr = enriched_df["atr"]

    bb_width = (enriched_df["bb_upper"] - enriched_df["bb_lower"]) / enriched_df["close"]
    ema_slope = (enriched_df["ema_trend"] - enriched_df["ema_trend"].shift(ema_slope_lookback)) / (
        atr * ema_slope_lookback
    )
    close_to_ema_atr = (enriched_df["close"] - enriched_df["ema_trend"]) / atr

    return pd.DataFrame(
        {
            "adx": enriched_df["adx"],
            "rsi": enriched_df["rsi"],
            "atr_percentile": enriched_df["atr_percentile"],
            "bb_width": bb_width,
            "ema_slope": ema_slope,
            "close_to_ema_atr": close_to_ema_atr,
        },
        index=enriched_df.index,
    )
