"""
News-event guardrail: blocks new entries and de-risks open positions around
high-impact macro releases. Standard stop-losses assume continuous pricing;
during major news releases liquidity can vanish and price gaps straight
through a normal ATR-based stop, so this is a separate, independent layer
from risk_management.py's per-trade sizing.

Two independent locks gate every new entry (check_market_clearance):
  a) Time lock: no High-impact event for either currency in the pair may
     fall within [-RESTRICT_BEFORE_MINUTES, +RESTRICT_AFTER_MINUTES] of now.
  b) Spread lock: even outside that window, the live spread must still be
     under MAX_ALLOWED_SPREAD_PIPS — news can leave the order book gapped or
     thin for a few extra minutes after the time lock itself clears.

manage_active_exposure() separately de-risks OPEN positions as a High-impact
event approaches (not just blocking new ones) — either moving the stop to
break-even or flattening the position entirely, per `protection_action`.
It returns a list of ProtectionAction recommendations rather than touching a
broker directly, so this module stays broker-agnostic and unit-testable;
main.py is what actually executes them via order_execution.py.

Fails safe throughout: if the calendar provider errors, check_market_
clearance blocks all entries and manage_active_exposure recommends closing
every open position, rather than trading blind through a news window
because the provider hiccuped.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from economic_calendar import CalendarProvider, CalendarUnavailableError, EconomicEvent
from logger_setup import get_logger

log = get_logger(__name__)

_HIGH_IMPACT = "High"
_VALID_PROTECTION_ACTIONS = ("close", "breakeven")


def split_currency_pair(pair: str) -> Tuple[str, str]:
    """'EURUSD', 'EUR_USD', 'EUR/USD', 'eur-usd' all -> ('EUR', 'USD')."""
    cleaned = pair.replace("_", "").replace("/", "").replace("-", "").upper()
    if len(cleaned) != 6:
        raise ValueError(f"Cannot parse currency pair {pair!r} (expected 6 letters after separators)")
    return cleaned[:3], cleaned[3:]


def points_to_pips(points: float) -> float:
    """MT5 reports spread in 'points', not pips. For 5-digit (or 3-digit
    JPY) broker pricing, 1 pip = 10 points — the same convention already
    documented in config.py's MAX_SPREAD_POINTS comment."""
    return points / 10.0


@dataclass(frozen=True)
class ProtectionAction:
    ticket: int
    symbol: str
    action: str  # "breakeven" or "close"
    reason: str


class EconomicCalendarFilter:
    def __init__(
        self,
        provider: CalendarProvider,
        restrict_before_minutes: int = 30,
        restrict_after_minutes: int = 30,
        max_allowed_spread_pips: float = 2.5,
        pre_event_protection_minutes: int = 5,
        protection_action: str = "close",
    ):
        if protection_action not in _VALID_PROTECTION_ACTIONS:
            raise ValueError(f"protection_action must be one of {_VALID_PROTECTION_ACTIONS}, got {protection_action!r}")

        self._provider = provider
        self._restrict_before = timedelta(minutes=restrict_before_minutes)
        self._restrict_after = timedelta(minutes=restrict_after_minutes)
        self._max_spread_pips = max_allowed_spread_pips
        self._pre_event_protection_minutes = pre_event_protection_minutes
        self._protection_action = protection_action

    def _get_events_safe(self) -> Optional[List[EconomicEvent]]:
        try:
            return self._provider.get_events()
        except CalendarUnavailableError:
            log.critical("Economic calendar unavailable - failing safe")
            return None
        except Exception:
            log.exception("Unexpected error fetching economic calendar - failing safe")
            return None

    @staticmethod
    def _high_impact_events_for(events: List[EconomicEvent], currencies: Tuple[str, str]) -> List[EconomicEvent]:
        return [e for e in events if e.impact == _HIGH_IMPACT and e.currency in currencies]

    def check_market_clearance(
        self, currency_pair: str, broker_spread_pips: float, now: Optional[datetime] = None
    ) -> bool:
        """
        Return True only if a new entry on `currency_pair` is safe right now:
        no High-impact event for either currency is within the configured
        time window, AND the live spread is under the configured cap.
        """
        now = now or datetime.now(timezone.utc)
        events = self._get_events_safe()
        if events is None:
            return False

        base, quote = split_currency_pair(currency_pair)

        for event in self._high_impact_events_for(events, (base, quote)):
            window_start = event.timestamp - self._restrict_before
            window_end = event.timestamp + self._restrict_after
            if window_start <= now <= window_end:
                log.warning(
                    "News time-lock: blocking %s entry - %r (%s) at %s is within "
                    "-%s/+%s of now (%s)",
                    currency_pair, event.title, event.currency, event.timestamp,
                    self._restrict_before, self._restrict_after, now,
                )
                return False

        if broker_spread_pips > self._max_spread_pips:
            log.warning(
                "News spread-lock: blocking %s entry - spread %.1f pips > max %.1f pips",
                currency_pair, broker_spread_pips, self._max_spread_pips,
            )
            return False

        return True

    def manage_active_exposure(self, open_positions, now: Optional[datetime] = None) -> List[ProtectionAction]:
        """
        Check every position in `open_positions` (objects with `.ticket` and
        `.symbol`, matching MT5 position objects) against the calendar.
        Returns a ProtectionAction for each position with a High-impact
        event for its currency due within `pre_event_protection_minutes`.
        Does not execute anything itself — main.py applies the returned
        actions via order_execution.py.
        """
        now = now or datetime.now(timezone.utc)
        events = self._get_events_safe()

        if events is None:
            return [
                ProtectionAction(p.ticket, p.symbol, "close", "economic calendar unavailable - failing safe")
                for p in open_positions
            ]

        actions = []
        for position in open_positions:
            base, quote = split_currency_pair(position.symbol)
            for event in self._high_impact_events_for(events, (base, quote)):
                minutes_until = (event.timestamp - now).total_seconds() / 60
                if 0 <= minutes_until <= self._pre_event_protection_minutes:
                    actions.append(
                        ProtectionAction(
                            position.ticket, position.symbol, self._protection_action,
                            f"{event.title!r} ({event.currency}) in {minutes_until:.1f} min",
                        )
                    )
                    break
        return actions


if __name__ == "__main__":
    from dataclasses import dataclass as _dataclass

    from economic_calendar import MockCalendarProvider

    news_time = datetime(2026, 7, 9, 18, 0, tzinfo=timezone.utc)  # FOMC-style 18:00 UTC release
    provider = MockCalendarProvider(
        [EconomicEvent(timestamp=news_time, impact="High", currency="USD", title="Federal Reserve Interest Rate Decision")]
    )
    news_guard = EconomicCalendarFilter(
        provider, restrict_before_minutes=30, restrict_after_minutes=30, max_allowed_spread_pips=2.5
    )

    print("=== EconomicCalendarFilter live timeline simulation ===")
    print(f"High-impact event: Federal Reserve Interest Rate Decision (USD) at {news_time.isoformat()}\n")

    # Note: the "31 minutes after" scenario replaces the brief's literal "2
    # minutes after" — at 2 minutes after, the time lock (active until +30
    # minutes) would already block the trade before the spread check ever
    # runs, so that scenario can't actually demonstrate the spread lock
    # firing independently. 31 minutes after is just past the time-lock
    # boundary, letting the spread lock be shown catching a still-wide
    # post-news spread on its own.
    scenarios = [
        (news_time - timedelta(minutes=10), 1.2, "10 min BEFORE the Fed decision, normal spread"),
        (news_time + timedelta(minutes=31), 4.0, "31 min AFTER (just past the time-lock window), spread still elevated"),
        (news_time + timedelta(minutes=35), 1.1, "35 min AFTER, spread back to normal"),
    ]
    for now, spread_pips, description in scenarios:
        cleared = news_guard.check_market_clearance("EUR_USD", spread_pips, now=now)
        status = "ALLOWED" if cleared else "BLOCKED"
        print(f"[{now.isoformat()}] {description}")
        print(f"    spread={spread_pips} pips -> {status}\n")

    print("=== manage_active_exposure simulation (protection_action='close') ===")

    @_dataclass
    class MockPosition:
        ticket: int
        symbol: str

    open_positions = [MockPosition(ticket=1001, symbol="EURUSD")]
    for now, description in [
        (news_time - timedelta(minutes=10), "10 min before (outside the 5-min protection window)"),
        (news_time - timedelta(minutes=3), "3 min before (inside the 5-min protection window)"),
    ]:
        actions = news_guard.manage_active_exposure(open_positions, now=now)
        print(f"[{now.isoformat()}] {description}\n    actions: {actions}\n")

    print("=== Fail-safe simulation (calendar provider errors) ===")

    class BrokenProvider(CalendarProvider):
        def get_events(self):
            raise CalendarUnavailableError("simulated API outage")

    broken_guard = EconomicCalendarFilter(BrokenProvider())
    cleared = broken_guard.check_market_clearance("EUR_USD", 1.0)
    print(f"Calendar provider down -> check_market_clearance returned {cleared} (must be False)")
    actions = broken_guard.manage_active_exposure(open_positions)
    print(f"Calendar provider down -> manage_active_exposure recommends: {actions}")
