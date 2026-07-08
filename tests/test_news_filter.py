from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from economic_calendar import CalendarProvider, CalendarUnavailableError, EconomicEvent, MockCalendarProvider
from news_filter import EconomicCalendarFilter, ProtectionAction, points_to_pips, split_currency_pair

NEWS_TIME = datetime(2026, 7, 9, 18, 0, tzinfo=timezone.utc)


def _fomc_event(**overrides):
    base = dict(timestamp=NEWS_TIME, impact="High", currency="USD", title="Fed Interest Rate Decision")
    base.update(overrides)
    return EconomicEvent(**base)


def _guard(events, **overrides):
    kwargs = dict(
        restrict_before_minutes=30, restrict_after_minutes=30,
        max_allowed_spread_pips=2.5, pre_event_protection_minutes=5, protection_action="close",
    )
    kwargs.update(overrides)
    return EconomicCalendarFilter(MockCalendarProvider(events), **kwargs)


class _BrokenProvider(CalendarProvider):
    def get_events(self):
        raise CalendarUnavailableError("simulated outage")


@dataclass
class _MockPosition:
    ticket: int
    symbol: str


class TestSplitCurrencyPair:
    @pytest.mark.parametrize("pair", ["EURUSD", "EUR_USD", "EUR/USD", "eur-usd"])
    def test_handles_common_formats(self, pair):
        assert split_currency_pair(pair) == ("EUR", "USD")

    def test_rejects_wrong_length(self):
        with pytest.raises(ValueError):
            split_currency_pair("EURO_DOLLAR")


class TestPointsToPips:
    def test_divides_by_ten(self):
        assert points_to_pips(25) == pytest.approx(2.5)


class TestCheckMarketClearance:
    def test_blocked_before_event(self):
        guard = _guard([_fomc_event()])
        assert guard.check_market_clearance("EURUSD", 1.0, now=NEWS_TIME - timedelta(minutes=10)) is False

    def test_blocked_after_event(self):
        guard = _guard([_fomc_event()])
        assert guard.check_market_clearance("EURUSD", 1.0, now=NEWS_TIME + timedelta(minutes=10)) is False

    def test_allowed_outside_window_with_normal_spread(self):
        guard = _guard([_fomc_event()])
        assert guard.check_market_clearance("EURUSD", 1.0, now=NEWS_TIME + timedelta(minutes=35)) is True

    def test_blocked_by_wide_spread_outside_time_window(self):
        guard = _guard([_fomc_event()])
        assert guard.check_market_clearance("EURUSD", 4.0, now=NEWS_TIME + timedelta(minutes=35)) is False

    def test_exactly_at_window_boundary_is_blocked(self):
        guard = _guard([_fomc_event()])
        assert guard.check_market_clearance("EURUSD", 1.0, now=NEWS_TIME + timedelta(minutes=30)) is False

    def test_ignores_medium_impact_events(self):
        guard = _guard([_fomc_event(impact="Medium")])
        assert guard.check_market_clearance("EURUSD", 1.0, now=NEWS_TIME) is True

    def test_ignores_unrelated_currency(self):
        guard = _guard([_fomc_event(currency="JPY")])
        assert guard.check_market_clearance("EURUSD", 1.0, now=NEWS_TIME) is True

    def test_matches_quote_currency_too(self):
        # USD is the quote currency of EURUSD, not just the base.
        guard = _guard([_fomc_event(currency="USD")])
        assert guard.check_market_clearance("GBPUSD", 1.0, now=NEWS_TIME) is False

    def test_fails_safe_when_provider_errors(self):
        guard = EconomicCalendarFilter(_BrokenProvider())
        assert guard.check_market_clearance("EURUSD", 1.0, now=NEWS_TIME) is False

    def test_rejects_invalid_protection_action(self):
        with pytest.raises(ValueError):
            EconomicCalendarFilter(MockCalendarProvider([]), protection_action="yolo")


class TestManageActiveExposure:
    def test_no_action_when_event_far_away(self):
        guard = _guard([_fomc_event()])
        positions = [_MockPosition(ticket=1, symbol="EURUSD")]
        assert guard.manage_active_exposure(positions, now=NEWS_TIME - timedelta(minutes=10)) == []

    def test_close_action_within_protection_window(self):
        guard = _guard([_fomc_event()], protection_action="close")
        positions = [_MockPosition(ticket=1, symbol="EURUSD")]
        actions = guard.manage_active_exposure(positions, now=NEWS_TIME - timedelta(minutes=3))
        assert len(actions) == 1
        assert actions[0] == ProtectionAction(1, "EURUSD", "close", actions[0].reason)

    def test_breakeven_action_when_configured(self):
        guard = _guard([_fomc_event()], protection_action="breakeven")
        positions = [_MockPosition(ticket=1, symbol="EURUSD")]
        actions = guard.manage_active_exposure(positions, now=NEWS_TIME - timedelta(minutes=3))
        assert actions[0].action == "breakeven"

    def test_only_affects_positions_on_affected_currency(self):
        guard = _guard([_fomc_event(currency="USD")])
        positions = [_MockPosition(ticket=1, symbol="EURUSD"), _MockPosition(ticket=2, symbol="EURGBP")]
        actions = guard.manage_active_exposure(positions, now=NEWS_TIME - timedelta(minutes=3))
        assert [a.ticket for a in actions] == [1]

    def test_fails_safe_recommends_closing_everything(self):
        guard = EconomicCalendarFilter(_BrokenProvider())
        positions = [_MockPosition(ticket=1, symbol="EURUSD"), _MockPosition(ticket=2, symbol="GBPJPY")]
        actions = guard.manage_active_exposure(positions, now=NEWS_TIME)
        assert {a.ticket for a in actions} == {1, 2}
        assert all(a.action == "close" for a in actions)

    def test_no_positions_no_actions(self):
        guard = _guard([_fomc_event()])
        assert guard.manage_active_exposure([], now=NEWS_TIME - timedelta(minutes=3)) == []
