"""
Multi-tier exit state machine.

For every open position this bot holds, tracks progress through three exit
tiers, expressed in multiples of the position's initial risk (R = the
original stop distance in price terms):

  a) +1R  -> move stop-loss to break-even (entry price). From this point
             the trade can no longer lose money, only give back unrealised
             profit.
  b) +2R  -> close PARTIAL_TP_FRACTION (default 50%) of the position,
             locking in a real gain.
  c) after the partial fires, the remaining runner is trailed with an
     ATR-based stop that only ever tightens, never loosens, letting winners
     run further while still protecting what's been gained.

State (has BE been applied? has the partial fired? the running high/low
water mark for trailing) is kept in an in-memory dict keyed by MT5 ticket,
backed by `trade_state_store` (sqlite) so it survives a bot restart.
`register_new_trade` seeds this accurately at entry time — that is the
authoritative source, since after SL is moved to break-even the *original*
stop distance can no longer be read back off the position. On startup,
state is reloaded from the database; only positions this process never saw
committed to disk (e.g. a DB file that predates this feature, or one opened
by another process) fall back to `_get_or_derive_state`'s best-effort
re-derivation from the position's current SL.
"""

import math

import MetaTrader5 as mt5

import broker
import config
import order_execution
import trade_state_store
from logger_setup import get_logger

log = get_logger(__name__)

_PRICE_EPSILON = 1e-9

# ticket -> {"initial_stop_distance", "be_applied", "partial_done", "extreme_price"}
_state = trade_state_store.load_all()


def register_new_trade(ticket, entry_price, initial_stop_distance):
    """Call immediately after a successful entry order to seed accurate
    R-multiple tracking for this position."""
    state = {
        "initial_stop_distance": initial_stop_distance,
        "be_applied": False,
        "partial_done": False,
        "extreme_price": entry_price,
    }
    _state[ticket] = state
    trade_state_store.save(ticket, state)


def prune_closed_positions(open_tickets):
    """Drop state for any ticket that is no longer an open position, to
    avoid an unbounded memory leak over a long-running process."""
    stale = [t for t in _state if t not in open_tickets]
    for t in stale:
        del _state[t]
    trade_state_store.delete_many(stale)


def _get_or_derive_state(position):
    ticket = position.ticket
    state = _state.get(ticket)
    if state is not None:
        return state

    # No memory of this ticket (likely a bot restart) — best-effort recovery.
    if not position.sl:
        log.warning(
            "Ticket %s has no stop-loss and no tracked state; skipping "
            "tiered exit management for it until it closes naturally.",
            ticket,
        )
        return None

    be_applied = abs(position.sl - position.price_open) < _PRICE_EPSILON
    initial_stop_distance = abs(position.price_open - position.sl)
    if initial_stop_distance == 0:
        return None

    state = {
        "initial_stop_distance": initial_stop_distance,
        "be_applied": be_applied,
        # We cannot know whether a partial was already taken pre-restart;
        # assume not, which is the conservative choice (worst case we
        # partial-close a bit late, never fail to protect the position).
        "partial_done": False,
        "extreme_price": position.price_open,
    }
    _state[ticket] = state
    trade_state_store.save(ticket, state)
    log.warning(
        "Re-derived exit state for pre-existing ticket %s after restart "
        "(be_applied=%s); partial-TP progress could not be recovered.",
        ticket, be_applied,
    )
    return state


def _r_multiple(position, direction, current_price, initial_stop_distance):
    if direction == "buy":
        profit_distance = current_price - position.price_open
    else:
        profit_distance = position.price_open - current_price
    return profit_distance / initial_stop_distance


def manage_open_positions(symbol, current_atr):
    """
    Evaluate and act on the exit tiers for every open position on `symbol`.
    `current_atr` is the latest ATR value for this symbol (from the same
    indicator computation used for signal generation), used for the
    trailing-stop leg.
    """
    positions = broker.get_open_positions(symbol=symbol)
    if not positions:
        return

    tick = broker.get_current_tick(symbol)

    for position in positions:
        state = _get_or_derive_state(position)
        if state is None:
            continue

        direction = "buy" if position.type == mt5.ORDER_TYPE_BUY else "sell"
        current_price = tick.bid if direction == "buy" else tick.ask
        r = _r_multiple(position, direction, current_price, state["initial_stop_distance"])

        # a) Break-even
        if not state["be_applied"] and r >= config.BREAKEVEN_TRIGGER_R:
            order_execution.modify_stop_loss(position, position.price_open)
            state["be_applied"] = True
            log.info("Ticket %s reached +%.2fR -> SL moved to break-even", position.ticket, r)

        # b) Partial take-profit
        if not state["partial_done"] and r >= config.PARTIAL_TP_TRIGGER_R:
            symbol_info = broker.get_symbol_info(symbol)
            raw_partial = position.volume * config.PARTIAL_TP_FRACTION
            steps = math.floor(raw_partial / symbol_info.volume_step)
            partial_volume = round(steps * symbol_info.volume_step, 8)

            if partial_volume >= symbol_info.volume_min:
                order_execution.close_position_partial(position, partial_volume, tick)
                state["partial_done"] = True
                log.info(
                    "Ticket %s reached +%.2fR -> partial close of %.2f lots",
                    position.ticket, r, partial_volume,
                )
            else:
                # Position too small to split further; treat as fully in
                # the "runner" phase so the trailing stop still engages.
                state["partial_done"] = True
                log.info(
                    "Ticket %s partial size below volume_min, skipping "
                    "split and moving straight to trailing-stop phase",
                    position.ticket,
                )

        # c) ATR trailing stop for the runner leg
        if state["partial_done"]:
            if direction == "buy":
                state["extreme_price"] = max(state["extreme_price"], current_price)
                trail_sl = state["extreme_price"] - current_atr * config.ATR_TRAIL_MULTIPLIER
                should_update = trail_sl > (position.sl or float("-inf"))
            else:
                state["extreme_price"] = min(state["extreme_price"], current_price)
                trail_sl = state["extreme_price"] + current_atr * config.ATR_TRAIL_MULTIPLIER
                should_update = trail_sl < (position.sl or float("inf"))

            if should_update:
                order_execution.modify_stop_loss(position, trail_sl)
                log.info("Ticket %s trailing stop tightened to %.5f", position.ticket, trail_sl)

        trade_state_store.save(position.ticket, state)
