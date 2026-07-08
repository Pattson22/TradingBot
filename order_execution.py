"""
Order placement and modification.

Every function here honours config.DRY_RUN: when True, requests are built
and logged in full but never sent to the broker. This lets the whole
pipeline (signal -> sizing -> order request) be exercised safely before any
real order-sending is enabled.

Guardrail this module exists to satisfy: the entry order's SL and TP are
attached to the SAME `order_send` request as the entry itself (MT5's
TRADE_ACTION_DEAL natively accepts `sl`/`tp` fields) — there is no instant
where a position is open without protective orders in place, even if the
bot process dies immediately after sending.
"""

import MetaTrader5 as mt5

import config
from logger_setup import get_logger

log = get_logger(__name__)

_ORDER_TYPE = {"buy": mt5.ORDER_TYPE_BUY, "sell": mt5.ORDER_TYPE_SELL}
_CLOSE_ORDER_TYPE = {"buy": mt5.ORDER_TYPE_SELL, "sell": mt5.ORDER_TYPE_BUY}


class OrderError(RuntimeError):
    """Raised when the broker rejects an order/modification request."""


def _check_result(result, context):
    if result is None:
        raise OrderError(f"{context}: order_send returned None ({mt5.last_error()})")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise OrderError(f"{context}: retcode={result.retcode} comment={result.comment}")
    return result


def send_market_order(symbol, direction, lot, stop_loss, take_profit, current_tick):
    """
    Send a market entry order with SL and TP attached atomically.

    direction: "buy" or "sell"
    current_tick: the MT5 tick object from broker.get_current_tick(symbol),
                  used to pick the correct fill price (ask for buy, bid for sell).
    """
    price = current_tick.ask if direction == "buy" else current_tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": _ORDER_TYPE[direction],
        "price": price,
        "sl": stop_loss,
        "tp": take_profit,
        "deviation": config.ORDER_DEVIATION_POINTS,
        "magic": config.MAGIC_NUMBER,
        "comment": "mean-reversion-bot",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    log.info(
        "%s %s %.2f lots @ %.5f | SL=%.5f TP=%.5f",
        "DRY-RUN entry" if config.DRY_RUN else "Sending entry",
        symbol, lot, price, stop_loss, take_profit,
    )

    if config.DRY_RUN:
        return None

    result = mt5.order_send(request)
    return _check_result(result, f"send_market_order({symbol})")


def modify_stop_loss(position, new_sl, new_tp=None):
    """
    Move a position's stop-loss (e.g. to break-even, or to trail it).
    Keeps the existing TP unless `new_tp` is explicitly provided.
    """
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": position.ticket,
        "symbol": position.symbol,
        "sl": new_sl,
        "tp": new_tp if new_tp is not None else position.tp,
    }

    log.info(
        "%s SL for ticket %s (%s) -> %.5f",
        "DRY-RUN modify" if config.DRY_RUN else "Modifying",
        position.ticket, position.symbol, new_sl,
    )

    if config.DRY_RUN:
        return None

    result = mt5.order_send(request)
    return _check_result(result, f"modify_stop_loss(ticket={position.ticket})")


def close_position_partial(position, volume_to_close, current_tick):
    """
    Close part of an open position (e.g. the 50% partial-profit tier).
    Sends an opposite-direction TRADE_ACTION_DEAL for `volume_to_close`,
    tagged with `position` so MT5 nets it against the existing ticket.
    """
    direction = "buy" if position.type == mt5.ORDER_TYPE_BUY else "sell"
    close_type = _CLOSE_ORDER_TYPE[direction]
    price = current_tick.bid if direction == "buy" else current_tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": position.symbol,
        "volume": volume_to_close,
        "type": close_type,
        "position": position.ticket,
        "price": price,
        "deviation": config.ORDER_DEVIATION_POINTS,
        "magic": config.MAGIC_NUMBER,
        "comment": "partial-tp",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    log.info(
        "%s partial close: ticket %s volume %.2f @ %.5f",
        "DRY-RUN" if config.DRY_RUN else "Executing",
        position.ticket, volume_to_close, price,
    )

    if config.DRY_RUN:
        return None

    result = mt5.order_send(request)
    return _check_result(result, f"close_position_partial(ticket={position.ticket})")


def close_position_full(position, current_tick):
    """Close an entire position immediately (used by the circuit breaker)."""
    return close_position_partial(position, position.volume, current_tick)
