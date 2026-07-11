"""
Daily drawdown circuit breaker.

Tracks account equity at the start of each calendar day (server/local date
of the machine running the bot). If equity ever falls MAX_DAILY_DRAWDOWN_PCT
below that day's starting value, every open position is force-closed and new
entries are halted until the date rolls over — capital preservation takes
priority over any in-progress trade thesis.

Baseline equity and the halt flag are persisted (circuit_breaker_store) and
restored on startup if they're from today -- this bot restarts often enough
(laptop reboots, crashes) that keeping this in memory only would let a
restart silently reset the day's drawdown budget or clear an active halt.
"""

from datetime import datetime, timezone

import broker
import circuit_breaker_store
import config
import order_execution
from logger_setup import get_logger

log = get_logger(__name__)


class CircuitBreaker:
    def __init__(self):
        self._current_day = None
        self._day_start_equity = None
        self._halted = False
        self._restore_persisted_state()

    def _today(self):
        return datetime.now(timezone.utc).date()

    def _restore_persisted_state(self):
        persisted = circuit_breaker_store.load()
        if persisted is None or persisted["day"] != str(self._today()):
            return
        self._current_day = self._today()
        self._day_start_equity = persisted["day_start_equity"]
        self._halted = persisted["halted"]
        log.info(
            "Restored circuit breaker state from disk: day_start_equity=%.2f halted=%s",
            self._day_start_equity, self._halted,
        )

    def maybe_roll_day(self, equity):
        """Call once per loop iteration. Detects a new calendar day and
        resets the halt + baseline equity for it."""
        today = self._today()
        if today != self._current_day:
            log.info(
                "New trading day (%s) detected. Resetting circuit breaker "
                "baseline equity to %.2f and clearing halt.",
                today, equity,
            )
            self._current_day = today
            self._day_start_equity = equity
            self._halted = False
            circuit_breaker_store.save(str(today), equity, False)

    def halted(self):
        return self._halted

    def check_drawdown(self):
        """
        Fetch current equity, compare to today's starting equity, and if
        the drawdown breaches the configured cap, close every open
        position across all traded symbols and set the halt flag.
        """
        snapshot = broker.get_account_snapshot()
        equity = snapshot["equity"]

        self.maybe_roll_day(equity)

        if self._halted:
            return

        drawdown_pct = (self._day_start_equity - equity) / self._day_start_equity
        if drawdown_pct >= config.MAX_DAILY_DRAWDOWN_PCT:
            log.critical(
                "CIRCUIT BREAKER TRIPPED: equity drawdown %.2f%% >= cap %.2f%% "
                "(day_start=%.2f, current=%.2f). Closing all positions and "
                "halting trading until the next calendar day.",
                drawdown_pct * 100, config.MAX_DAILY_DRAWDOWN_PCT * 100,
                self._day_start_equity, equity,
            )
            self._close_everything()
            self._halted = True
            circuit_breaker_store.save(str(self._current_day), self._day_start_equity, True)

    def _close_everything(self):
        for symbol in config.SYMBOLS:
            positions = broker.get_open_positions(symbol=symbol)
            if not positions:
                continue
            tick = broker.get_current_tick(symbol)
            for position in positions:
                try:
                    order_execution.close_position_full(position, tick)
                except Exception:
                    log.exception(
                        "Failed to close ticket %s during circuit-breaker "
                        "shutdown — manual intervention may be required.",
                        position.ticket,
                    )
