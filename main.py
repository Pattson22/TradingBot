"""
Main control loop.

Wires together every module: on each tick, per configured symbol, it first
manages any existing position (break-even / partial-TP / trailing stop),
then — if flat on that symbol and the circuit breaker isn't halted and the
spread is acceptable — evaluates the strategy for a fresh entry and sizes it
against the CURRENT account balance.

Run with `python main.py`. Stop with Ctrl+C for a clean disconnect.
"""

import time

import broker
import circuit_breaker
import config
import data_feed
import order_execution
import risk_management
import signals
import spread_filter
import trade_manager
from indicators import compute_all
from logger_setup import configure_logging, get_logger

log = get_logger(__name__)


def _manage_symbol(symbol, enriched_df):
    current_atr = enriched_df["atr"].iloc[-1]
    trade_manager.manage_open_positions(symbol, current_atr)


def _attempt_entry(symbol, df, mtf_df):
    symbol_info = broker.get_symbol_info(symbol)
    if not spread_filter.is_spread_acceptable(symbol_info):
        return

    signal = signals.generate(df, mtf_df)
    if signal is None:
        return

    log.info("Signal on %s: %s (%s)", symbol, signal.direction, signal.reason)

    levels = risk_management.calculate_trade_levels(
        signal.direction,
        signal.entry_price,
        signal.atr,
        config.ATR_SL_MULTIPLIER,
        config.ATR_TP_MULTIPLIER,
    )

    account = broker.get_account_snapshot()
    lot = risk_management.calculate_lot_size(
        balance=account["balance"],
        risk_per_trade=config.RISK_PER_TRADE,
        stop_distance_price=levels.stop_distance,
        tick_size=symbol_info.trade_tick_size,
        tick_value=symbol_info.trade_tick_value,
        volume_step=symbol_info.volume_step,
        volume_min=symbol_info.volume_min,
        volume_max=symbol_info.volume_max,
    )
    if lot is None:
        log.info("Skipping %s signal: computed lot size below broker minimum", symbol)
        return

    tick = broker.get_current_tick(symbol)
    result = order_execution.send_market_order(
        symbol, signal.direction, lot, levels.stop_loss, levels.take_profit, tick
    )

    if config.DRY_RUN:
        return

    ticket = result.order
    trade_manager.register_new_trade(ticket, signal.entry_price, levels.stop_distance)
    log.info(
        "Entered %s %s: ticket=%s lot=%.2f SL=%.5f TP=%.5f",
        symbol, signal.direction, ticket, lot, levels.stop_loss, levels.take_profit,
    )


def _run_one_cycle(breaker):
    breaker.check_drawdown()

    if breaker.halted():
        log.warning("Circuit breaker halted - skipping this cycle's entries")

    all_open_tickets = set()

    for symbol in config.SYMBOLS:
        df = data_feed.get_ohlc(symbol)
        enriched = compute_all(df, config)

        _manage_symbol(symbol, enriched)

        open_positions = broker.get_open_positions(symbol=symbol)
        all_open_tickets.update(p.ticket for p in open_positions)

        if not breaker.halted() and not open_positions:
            mtf_df = data_feed.get_ohlc(
                symbol, timeframe_name=config.MTF_TIMEFRAME_NAME, bars=config.MTF_BARS_TO_FETCH
            )
            _attempt_entry(symbol, df, mtf_df)

    trade_manager.prune_closed_positions(all_open_tickets)


def run():
    configure_logging()
    broker.connect()
    breaker = circuit_breaker.CircuitBreaker()

    if config.DRY_RUN:
        log.warning("DRY_RUN is enabled: no live orders will be sent.")

    try:
        while True:
            try:
                _run_one_cycle(breaker)
            except Exception:
                log.exception("Unhandled error in main loop iteration - continuing")

            time.sleep(config.POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        log.info("Shutdown requested (KeyboardInterrupt)")
    finally:
        broker.disconnect()


if __name__ == "__main__":
    run()
