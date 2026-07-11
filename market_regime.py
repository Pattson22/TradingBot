"""
Volatility-driven market regime classification (ADX-based).

Trending vs Ranging isn't just descriptive here -- signals.py and
backtest_engine.py (opt-in via config.REGIME_ADAPTIVE_RSI_ENABLED) use it to
pick different RSI extreme thresholds: Ranging asks for a deeper RSI extreme
before fading price back toward the mean (no reliable directional edge, so
entries should avoid chop), Trending accepts a shallower pullback since a
strong trend may not give a full mean-reversion extreme before continuing.

Like the session/volatility filters in config.py, this stays opt-in (OFF by
default) until backtest.py/optimize.py show it actually helps -- don't flip
it on as a live default without that evidence.
"""

from __future__ import annotations

import math
from typing import Optional

TRENDING = "trending"
RANGING = "ranging"


def classify(adx_value: float, trending_threshold: float) -> Optional[str]:
    """Classify the current regime from a single ADX reading.

    Returns None if `adx_value` is NaN (indicator not yet warmed up) --
    callers should treat None the same as any other not-ready indicator and
    skip the signal check for that bar, consistent with signals.py's
    existing warm-up handling.
    """
    if math.isnan(adx_value):
        return None
    return TRENDING if adx_value > trending_threshold else RANGING
