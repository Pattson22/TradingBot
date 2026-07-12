"""
ML market-regime classifier: a learned alternative to market_regime.classify()'s
fixed ADX>25 threshold, trained on bar-level history instead of a single
hand-picked cutoff.

Training label ("was this bar actually inside a trending stretch?") is
FORWARD-looking by necessity -- any supervised label is -- but that forward
window is used ONLY to build the training target here, in train()/
build_labels(). It is never available to predict(), and predict() is the
only function called on out-of-sample or live data. build_labels() must
therefore only ever be called on an in-sample window; a walk-forward
harness that called it on out-of-sample data would be leaking the future
into model selection, exactly the mistake backtest_optimize.py's whole
fold structure exists to prevent.

Label construction: forward realized trend strength over `forward_bars`,
ATR-normalized (so it's comparable across volatility regimes) --

    trend_strength[t] = |close[t+forward_bars] - close[t]| / (atr[t] * sqrt(forward_bars))

-- thresholded at that window's own median (computed fresh per training
call, never reused across folds) to produce a balanced trending/ranging
split. Deliberately NOT "did ADX say >25" -- training against the existing
rule's own output would just teach the model to imitate it.

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
MIN_TRAINING_SAMPLES = 30


def build_labels(enriched_df: pd.DataFrame, forward_bars: int) -> Tuple[pd.Series, float]:
    """Forward-looking training labels for `enriched_df` -- ONLY valid to
    call on an in-sample window, never on out-of-sample/live data (see
    module docstring). Returns (labels, threshold_used); labels are
    market_regime.TRENDING / market_regime.RANGING / None (None where the
    forward window runs past the end of the data)."""
    close = enriched_df["close"]
    atr = enriched_df["atr"]
    future_close = close.shift(-forward_bars)
    trend_strength = (future_close - close).abs() / (atr * (forward_bars ** 0.5))

    threshold = trend_strength.median()
    valid = trend_strength.notna()

    labels = pd.Series(None, index=enriched_df.index, dtype=object)
    labels.loc[valid] = trend_strength.loc[valid].apply(
        lambda v: market_regime.TRENDING if v > threshold else market_regime.RANGING
    )
    return labels, threshold


def train(
    enriched_df: pd.DataFrame,
    forward_bars: int = DEFAULT_FORWARD_BARS,
    random_state: int = 42,
) -> Tuple[HistGradientBoostingClassifier, float]:
    """Train a regime classifier on `enriched_df` (an in-sample window --
    see module docstring). Returns (model, label_threshold)."""
    features = regime_features.build_features(enriched_df)
    labels, threshold = build_labels(enriched_df, forward_bars)

    valid = features.notna().all(axis=1) & labels.notna()
    x_train = features.loc[valid]
    y_train = labels.loc[valid]

    if len(x_train) < MIN_TRAINING_SAMPLES:
        raise ValueError(
            f"Only {len(x_train)} valid training rows after dropping warm-up/forward-label "
            f"NaNs -- need at least {MIN_TRAINING_SAMPLES}. Use a larger in-sample window."
        )

    model = HistGradientBoostingClassifier(max_depth=4, min_samples_leaf=30, random_state=random_state)
    model.fit(x_train, y_train)

    log.info(
        "Trained regime classifier on %d rows (forward_bars=%d, label_threshold=%.4f)",
        len(x_train), forward_bars, threshold,
    )
    return model, threshold


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


def save(model: HistGradientBoostingClassifier, threshold: float, forward_bars: int, path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    joblib.dump({"model": model, "threshold": threshold, "forward_bars": forward_bars}, path)


def load(path: str) -> Tuple[HistGradientBoostingClassifier, float, int]:
    artifact = joblib.load(path)
    return artifact["model"], artifact["threshold"], artifact["forward_bars"]
