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
import economic_calendar
import news_filter
import order_execution
import risk_management
import signals
import spread_filter
import trade_manager
from indicators import compute_all
from logger_setup import configure_logging, get_logger

log = get_logger(__name__)


def _build_news_guard():
    """Opt-in: only built if NEWS_CALENDAR_URL is configured, so the news
    filter doesn't change behaviour for anyone who hasn't set it up.

    Wired to jblanked.com's Calendar API specifically (see config.py's
    NEWS_CALENDAR_URL comment) -- swap the header scheme and transform if
    you point this at a different provider later."""
    if not config.NEWS_CALENDAR_URL:
        log.warning("NEWS_CALENDAR_URL not set - economic calendar news filter is DISABLED")
        return None

    headers = {"Authorization": f"Api-Key {config.NEWS_CALENDAR_API_KEY}"} if config.NEWS_CALENDAR_API_KEY else {}
    raw_provider = economic_calendar.HttpJsonCalendarProvider(
        config.NEWS_CALENDAR_URL, headers=headers, transform=economic_calendar.transform_jblanked_event,
    )
    provider = economic_calendar.CachingCalendarProvider(
        raw_provider, ttl_seconds=config.NEWS_CALENDAR_CACHE_TTL_SECONDS,
    )
    return news_filter.EconomicCalendarFilter(
        provider,
        restrict_before_minutes=config.NEWS_RESTRICT_BEFORE_MINUTES,
        restrict_after_minutes=config.NEWS_RESTRICT_AFTER_MINUTES,
        max_allowed_spread_pips=config.NEWS_MAX_ALLOWED_SPREAD_PIPS,
        pre_event_protection_minutes=config.NEWS_PRE_EVENT_PROTECTION_MINUTES,
        protection_action=config.NEWS_PROTECTION_ACTION,
    )


def _apply_news_protection(news_guard, symbol, open_positions):
    """De-risk existing positions ahead of high-impact news. Applied once
    per cycle; a position closed here still shows in this cycle's
    `open_positions` snapshot, but prune_closed_positions() and the next
    poll both catch up within one cycle, same tolerance as trade_manager's
    other single-action-per-cycle behaviour."""
    if news_guard is None or not open_positions:
        return

    actions = news_guard.manage_active_exposure(open_positions)
    if not actions:
        return

    tick = broker.get_current_tick(symbol)
    positions_by_ticket = {p.ticket: p for p in open_positions}
    for action in actions:
        position = positions_by_ticket.get(action.ticket)
        if position is None:
            continue
        log.warning(
            "News protection triggered for ticket %s (%s): %s -> %s",
            action.ticket, action.symbol, action.reason, action.action,
        )
        if action.action == "close":
            order_execution.close_position_full(position, tick)
        elif action.action == "breakeven":
            order_execution.modify_stop_loss(position, position.price_open)


def _manage_symbol(symbol, enriched_df):
    current_atr = enriched_df["atr"].iloc[-1]
    trade_manager.manage_open_positions(symbol, current_atr)


def _attempt_entry(symbol, df, mtf_df, news_guard):
    symbol_info = broker.get_symbol_info(symbol)
    if not spread_filter.is_spread_acceptable(symbol_info):
        return

    if news_guard is not None:
        spread_pips = news_filter.points_to_pips(symbol_info.spread)
        if not news_guard.check_market_clearance(symbol, spread_pips):
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


def _run_one_cycle(breaker, news_guard):
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
        _apply_news_protection(news_guard, symbol, open_positions)

        if not breaker.halted() and not open_positions:
            mtf_df = data_feed.get_ohlc(
                symbol, timeframe_name=config.MTF_TIMEFRAME_NAME, bars=config.MTF_BARS_TO_FETCH
            )
            _attempt_entry(symbol, df, mtf_df, news_guard)

    trade_manager.prune_closed_positions(all_open_tickets)


RECONNECT_MAX_ATTEMPTS = 5
RECONNECT_BASE_DELAY_SECONDS = 10


def _reconnect_with_backoff():
    """Retry broker.reconnect() with linearly increasing backoff. Returns
    True once reconnected, False if every attempt in this call failed (the
    next poll cycle's own BrokerConnectionError will trigger another round,
    so a temporarily-unreachable terminal keeps getting retried indefinitely
    rather than leaving the bot stuck logging the same failure forever)."""
    for attempt in range(1, RECONNECT_MAX_ATTEMPTS + 1):
        delay = RECONNECT_BASE_DELAY_SECONDS * attempt
        log.warning("Reconnect attempt %d/%d in %ds...", attempt, RECONNECT_MAX_ATTEMPTS, delay)
        time.sleep(delay)
        try:
            broker.reconnect()
            log.info("Reconnected to MT5 successfully")
            return True
        except broker.BrokerConnectionError:
            log.exception("Reconnect attempt %d/%d failed", attempt, RECONNECT_MAX_ATTEMPTS)
    log.error("All reconnect attempts failed this cycle - will try again next cycle")
    return False


def run():
    configure_logging()
    broker.connect()
    breaker = circuit_breaker.CircuitBreaker()
    news_guard = _build_news_guard()

    if config.DRY_RUN:
        log.warning("DRY_RUN is enabled: no live orders will be sent.")

    try:
        while True:
            try:
                _run_one_cycle(breaker, news_guard)
            except broker.BrokerConnectionError:
                log.exception("Broker connection lost")
                _reconnect_with_backoff()
            except Exception:
                log.exception("Unhandled error in main loop iteration - continuing")

            time.sleep(config.POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        log.info("Shutdown requested (KeyboardInterrupt)")
    finally:
        broker.disconnect()


if __name__ == "__main__":
    run()
