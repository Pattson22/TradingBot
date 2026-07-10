"""
The core risk engine: converts (balance, ATR, symbol tick economics) into an
exact lot size that risks a fixed fraction of the account, never more.

The sizing math is deliberately pure/stateless (plain floats in, floats out,
no MT5 import) so it can be unit tested or reused against a backtest without
a live terminal connection. `broker.py` / `main.py` supply the live numbers.

--------------------------------------------------------------------------
WORKED EXAMPLE (numbers are illustrative, not live quotes)
--------------------------------------------------------------------------
Account balance:            $10,000.00
Risk per trade:              1%      -> risk_amount = $100.00
ATR(14) on EURUSD H1:        0.00120 (12 pips)
SL multiplier:                1.5     -> stop_distance_price = 0.00180 (18 pips)
Symbol tick_size (5-digit):   0.00001
Symbol tick_value (per 1.0 lot, from broker): $1.00 per tick move
  (i.e. a 1.0 lot EURUSD position moving 1 tick = 0.00001 changes P/L by $1)

ticks_at_risk        = stop_distance_price / tick_size
                      = 0.00180 / 0.00001 = 180 ticks
monetary_risk_per_lot = ticks_at_risk * tick_value
                      = 180 * $1.00 = $180.00 risked per 1.0 lot if SL is hit

lot_size = risk_amount / monetary_risk_per_lot
         = $100.00 / $180.00 = 0.5556 lots

Rounded DOWN to a 0.01 volume_step -> 0.55 lots.
Actual risk at 0.55 lots = 0.55 * $180.00 = $99.00 <= $100.00 cap.  (Never
rounding up keeps us strictly under the 1% ceiling, at the cost of a
fraction of a percent of unused risk capacity — an acceptable trade-off for
a capital-preservation-first system.)
--------------------------------------------------------------------------
"""

import math
from collections import namedtuple

from logger_setup import get_logger

log = get_logger(__name__)

TradeLevels = namedtuple("TradeLevels", ["stop_loss", "take_profit", "stop_distance"])


def calculate_stop_distance(atr_value, sl_multiplier):
    """ATR-based stop distance in price terms. Volatility-adaptive: widens
    in choppy/volatile markets, tightens in quiet ones, instead of a fixed
    pip value that would be too tight or too loose depending on conditions.
    """
    if atr_value <= 0:
        raise ValueError("ATR must be positive to compute a stop distance")
    return atr_value * sl_multiplier


def calculate_trade_levels(direction, entry_price, atr_value, sl_multiplier, tp_multiplier):
    """
    Compute stop-loss and initial (safety-net) take-profit prices for a new
    trade, given its direction ("buy"/"sell"), entry price, and current ATR.
    """
    stop_distance = calculate_stop_distance(atr_value, sl_multiplier)
    tp_distance = atr_value * tp_multiplier

    if direction == "buy":
        stop_loss = entry_price - stop_distance
        take_profit = entry_price + tp_distance
    elif direction == "sell":
        stop_loss = entry_price + stop_distance
        take_profit = entry_price - tp_distance
    else:
        raise ValueError(f"direction must be 'buy' or 'sell', got {direction!r}")

    return TradeLevels(stop_loss=stop_loss, take_profit=take_profit, stop_distance=stop_distance)


def calculate_position_risk(direction, entry_price, stop_loss, volume, tick_size, tick_value):
    """
    Current worst-case loss (in account currency) if `stop_loss` is hit.

    Works for both an already-open position (entry_price=its price_open,
    stop_loss=its CURRENT sl, which trade_manager.py may have already moved
    to break-even or trailed into profit) and a not-yet-sent proposed trade
    (entry_price=signal price, stop_loss=the freshly computed level) -- the
    math is identical either way, which is what lets main.py sum this
    across open positions AND a new candidate trade to enforce
    config.MAX_PORTFOLIO_RISK_PCT.

    Returns 0.0 once the stop is at or past break-even (no further downside
    on this leg), rather than a negative number -- a de-risked position
    shouldn't ever *subtract* from other positions' risk, just stop adding
    to it.
    """
    if direction == "buy":
        losing_distance = entry_price - stop_loss
    elif direction == "sell":
        losing_distance = stop_loss - entry_price
    else:
        raise ValueError(f"direction must be 'buy' or 'sell', got {direction!r}")

    if losing_distance <= 0:
        return 0.0

    ticks = losing_distance / tick_size
    return ticks * tick_value * volume


def calculate_lot_size(
    balance,
    risk_per_trade,
    stop_distance_price,
    tick_size,
    tick_value,
    volume_step,
    volume_min,
    volume_max,
):
    """
    Fixed-fractional position sizing.

    Returns the lot size (float, rounded DOWN to `volume_step`) that risks
    at most `risk_per_trade` fraction of `balance` if price moves
    `stop_distance_price` against the position, using the broker-provided
    tick economics for this symbol.

    Returns None if the computed size would round down below `volume_min`
    (i.e. even the smallest tradable lot would risk more than the cap
    allows) — in that case the correct action is to SKIP the trade, never
    to round up and silently exceed the risk cap.
    """
    if balance <= 0:
        raise ValueError("balance must be positive")
    if stop_distance_price <= 0:
        raise ValueError("stop_distance_price must be positive")
    if tick_size <= 0 or tick_value <= 0:
        raise ValueError("tick_size and tick_value must be positive")

    risk_amount = balance * risk_per_trade

    ticks_at_risk = stop_distance_price / tick_size
    monetary_risk_per_lot = ticks_at_risk * tick_value

    raw_lot = risk_amount / monetary_risk_per_lot

    # Round DOWN to the broker's allowed volume increment.
    steps = math.floor(raw_lot / volume_step)
    lot = steps * volume_step
    lot = round(lot, 8)  # guard against float drift before comparisons

    if lot < volume_min:
        log.info(
            "Computed lot %.4f below volume_min %.4f (risk_amount=%.2f, "
            "monetary_risk_per_lot=%.2f) -> skipping trade",
            raw_lot, volume_min, risk_amount, monetary_risk_per_lot,
        )
        return None

    lot = min(lot, volume_max)

    actual_risk = lot * monetary_risk_per_lot
    log.info(
        "Position size: balance=%.2f risk_amount=%.2f stop_distance=%.5f "
        "-> lot=%.2f (actual risk=%.2f, %.3f%% of balance)",
        balance, risk_amount, stop_distance_price, lot,
        actual_risk, 100 * actual_risk / balance,
    )
    return lot
