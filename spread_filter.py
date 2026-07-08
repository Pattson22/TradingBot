"""
Spread guard: blocks new entries when the current bid/ask spread is wider
than the configured baseline for that symbol (e.g. around major news
releases, thin liquidity at session opens/closes, or broker feed issues).

Does not affect already-open positions — those remain governed solely by
their SL/TP/trailing-stop logic.
"""

import config
from logger_setup import get_logger

log = get_logger(__name__)


def is_spread_acceptable(symbol_info):
    """
    symbol_info: the MT5 SymbolInfo for the symbol in question (has a
    `.spread` attribute in broker points).
    """
    max_allowed = config.MAX_SPREAD_POINTS.get(symbol_info.name, config.DEFAULT_MAX_SPREAD_POINTS)
    if symbol_info.spread > max_allowed:
        log.info(
            "Spread filter blocked entry on %s: current spread %d points > max %d points",
            symbol_info.name, symbol_info.spread, max_allowed,
        )
        return False
    return True
