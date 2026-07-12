"""
ML market-regime classifier: a learned alternative to market_regime.py's
fixed ADX>25 threshold, trained on bar-level history instead of a single
hand-picked cutoff.

Label construction is DIRECTLY ALIGNED to the actual decision this
classifier drives (regime-adaptive RSI thresholds loosen or tighten the
strategy's RSI gate) -- not an indirect proxy. A first attempt at this
module trained against forward realized trend strength (was the market
generally trending after this bar?), which turned out not to correlate
well with the real question and lost to the plain ADX rule out-of-sample
(see regime_classifier_optimize.py). This version asks the real question
directly: at every bar where the strategy's trend+Bollinger setup fires --
IGNORING the RSI extreme gate, since that gate is exactly what regime
classification decides whether to loosen or tighten -- simulate the
hypothetical trade that setup would produce (same SL/TP sizing as
risk_management.calculate_trade_levels) and label the bar TRENDING (loosen
the gate) if it would have won, RANGING (tighten it) if it would have lost
or stayed unresolved. Bars with no qualifying setup get no label at all --
the RSI-threshold decision is moot there, since no trade fires under
either regime.

That label construction is forward-looking by necessity (any supervised
label is), so build_labels() must ONLY ever be called on an in-sample
window -- never out-of-sample or live data, which is exactly the future
information a real bot wouldn't have. predict() is the only function
called on out-of-sample/live data, and it uses no forward-looking
information.

Features come from regime_features.build_features(), shared with predict()
so the two paths can never compute a feature differently by accident.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

import joblib
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

import market_regime
import regime_features
from logger_setup import get_logger

log = get_logger(__name__)

DEFAULT_FORWARD_BARS = 24  # ~1 trading day of H1 bars
DEFAULT_SL_MULTIPLIER = 1.5      # matches config.ATR_SL_MULTIPLIER's default
DEFAULT_RISK_REWARD_RATIO = 3.0  # matches config.RISK_REWARD_RATIO's default
MIN_TRAINING_SAMPLES = 30


def _setup_direction(row) -> Optional[str]:
    """The strategy's trend+Bollinger setup direction (signals.py /
    backtest_engine._check_entry's bullish_trend/bearish_trend + band-touch
    conditions), WITHOUT the RSI extreme gate -- that gate is the thing
    regime classification decides whether to loosen or tighten, so
    including it here would make the label circular."""
    bullish = row.close > row.ema_trend and row.mtf_trend == "bullish" and row.close <= row.bb_lower
    bearish = row.close < row.ema_trend and row.mtf_trend == "bearish" and row.close >= row.bb_upper
    if bullish:
        return "buy"
    if bearish:
        return "sell"
    return None


def _simulate_outcome(future_rows, direction, entry_price, atr_value, sl_multiplier, risk_reward_ratio) -> bool:
    """True if a hypothetical trade opened at `entry_price` in `direction`
    would hit take-profit before stop-loss within `future_rows` (an
    iterable of high/low rows immediately following the entry bar). SL
    wins same-bar ties, matching backtest_engine._manage_position's
    convention. False if stop-loss hits first or neither level is reached
    within the window (unresolved -> conservative "not a win")."""
    stop_distance = atr_value * sl_multiplier
    tp_distance = stop_distance * risk_reward_ratio

    if direction == "buy":
        stop_loss = entry_price - stop_distance
        take_profit = entry_price + tp_distance
    else:
        stop_loss = entry_price + stop_distance
        take_profit = entry_price - tp_distance

    for future in future_rows:
        if direction == "buy":
            sl_hit = future.low <= stop_loss
            tp_hit = future.high >= take_profit
        else:
            sl_hit = future.high >= stop_loss
            tp_hit = future.low <= take_profit

        if sl_hit:
            return False
        if tp_hit:
            return True
    return False


def build_labels(
    enriched_df: pd.DataFrame,
    forward_bars: int,
    sl_multiplier: float = DEFAULT_SL_MULTIPLIER,
    risk_reward_ratio: float = DEFAULT_RISK_REWARD_RATIO,
) -> pd.Series:
    """Aligned-target training labels for `enriched_df` -- ONLY valid to
    call on an in-sample window, never on out-of-sample/live data (see
    module docstring). Requires close/ema_trend/bb_lower/bb_upper/atr/
    mtf_trend/high/low columns. Returns market_regime.TRENDING /
    market_regime.RANGING / None per bar (None where there's no qualifying
    setup, or not enough forward bars left to resolve one)."""
    n = len(enriched_df)
    labels = pd.Series(None, index=enriched_df.index, dtype=object)
    rows = list(enriched_df.itertuples())

    for pos in range(n - forward_bars):
        row = rows[pos]
        direction = _setup_direction(row)
        if direction is None:
            continue
        won = _simulate_outcome(
            rows[pos + 1: pos + 1 + forward_bars], direction, row.close, row.atr, sl_multiplier, risk_reward_ratio,
        )
        labels.iloc[pos] = market_regime.TRENDING if won else market_regime.RANGING

    return labels


def train(
    enriched_df: pd.DataFrame,
    forward_bars: int = DEFAULT_FORWARD_BARS,
    sl_multiplier: float = DEFAULT_SL_MULTIPLIER,
    risk_reward_ratio: float = DEFAULT_RISK_REWARD_RATIO,
    random_state: int = 42,
) -> Tuple[HistGradientBoostingClassifier, int]:
    """Train a regime classifier on `enriched_df` (an in-sample window --
    see module docstring). Returns (model, n_training_samples)."""
    features = regime_features.build_features(enriched_df)
    labels = build_labels(enriched_df, forward_bars, sl_multiplier, risk_reward_ratio)

    valid = features.notna().all(axis=1) & labels.notna()
    x_train = features.loc[valid]
    y_train = labels.loc[valid]

    if len(x_train) < MIN_TRAINING_SAMPLES:
        raise ValueError(
            f"Only {len(x_train)} labeled setup bars after dropping warm-up/unresolved rows -- "
            f"need at least {MIN_TRAINING_SAMPLES}. Use a larger in-sample window."
        )

    model = HistGradientBoostingClassifier(max_depth=4, min_samples_leaf=30, random_state=random_state)
    model.fit(x_train, y_train)

    log.info(
        "Trained regime classifier on %d labeled setup bars (forward_bars=%d, sl_multiplier=%.2f, risk_reward_ratio=%.2f)",
        len(x_train), forward_bars, sl_multiplier, risk_reward_ratio,
    )
    return model, len(x_train)


def predict(model: HistGradientBoostingClassifier, enriched_df: pd.DataFrame) -> pd.Series:
    """Predict market_regime.TRENDING / RANGING for every row of
    `enriched_df` with fully warmed-up features; None elsewhere. Safe to
    call on out-of-sample or live data -- unlike build_labels(), this uses
    no forward-looking information."""
    features = regime_features.build_features(enriched_df)
    valid = features.notna().all(axis=1)

    predictions = pd.Series(None, index=enriched_df.index, dtype=object)
    if valid.any():
        predictions.loc[valid] = model.predict(features.loc[valid])
    return predictions


def save(model: HistGradientBoostingClassifier, forward_bars: int, sl_multiplier: float, risk_reward_ratio: float, path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "forward_bars": forward_bars,
            "sl_multiplier": sl_multiplier,
            "risk_reward_ratio": risk_reward_ratio,
        },
        path,
    )


def load(path: str) -> Tuple[HistGradientBoostingClassifier, int, float, float]:
    artifact = joblib.load(path)
    return artifact["model"], artifact["forward_bars"], artifact["sl_multiplier"], artifact["risk_reward_ratio"]
