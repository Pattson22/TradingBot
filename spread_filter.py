"""
Spread guard: blocks new entries when the current bid/ask spread is wider
than either (a) the symbol's static ceiling (config.MAX_SPREAD_POINTS -- a
hard sanity bound against broker glitches or genuinely abnormal readings)
or (b), once enough history has accumulated, a rolling multiple of that
symbol's own recent average spread (config.SPREAD_ROLLING_MULTIPLIER) --
adapts to each symbol's actual typical spread instead of one fixed number,
catching e.g. a spike to 2x-normal that's still comfortably under the
static cap.

Readings that already fail the static cap are never added to the rolling
history -- letting true outliers into the average would drag the adaptive
threshold up after every spike, weakening the very protection meant to
catch the next one.

Rolling history lives in memory only, per symbol, and resets on every bot
restart (this bot restarts often -- laptop reboots, crashes); until
SPREAD_ROLLING_MIN_SAMPLES readings have accumulated again, only the static
cap applies. Unlike circuit_breaker's daily baseline, a lost rolling window
just means fewer entries get the adaptive check for a while, not a safety
regression, so this was judged not worth persisting to disk.

Does not affect already-open positions -- those remain governed solely by
their SL/TP/trailing-stop logic.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, Optional

import config
from logger_setup import get_logger

log = get_logger(__name__)

_spread_history: Dict[str, Deque[float]] = {}


def _rolling_mean(history: Deque[float]) -> Optional[float]:
    if len(history) < config.SPREAD_ROLLING_MIN_SAMPLES:
        return None
    return sum(history) / len(history)


def is_spread_acceptable(symbol_info) -> bool:
    """
    symbol_info: the MT5 SymbolInfo for the symbol in question (has a
    `.spread` attribute in broker points).
    """
    symbol = symbol_info.name
    spread = symbol_info.spread
    history = _spread_history.setdefault(symbol, deque(maxlen=config.SPREAD_ROLLING_WINDOW))

    static_cap = config.MAX_SPREAD_POINTS.get(symbol, config.DEFAULT_MAX_SPREAD_POINTS)
    if spread > static_cap:
        log.info(
            "Spread filter blocked entry on %s: current spread %d points > static cap %d points",
            symbol, spread, static_cap,
        )
        return False

    rolling_mean = _rolling_mean(history)
    if rolling_mean is not None:
        adaptive_cap = rolling_mean * config.SPREAD_ROLLING_MULTIPLIER
        if spread > adaptive_cap:
            log.info(
                "Spread filter blocked entry on %s: current spread %d points > "
                "rolling mean %.1f x %.1f = %.1f points (static cap %d)",
                symbol, spread, rolling_mean, config.SPREAD_ROLLING_MULTIPLIER,
                adaptive_cap, static_cap,
            )
            history.append(spread)
            return False

    history.append(spread)
    return True
