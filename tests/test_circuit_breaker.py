import datetime
from unittest.mock import MagicMock

import pytest

import broker
import circuit_breaker
import circuit_breaker_store
import config
import order_execution

TODAY = datetime.date(2026, 7, 11)
YESTERDAY = datetime.date(2026, 7, 10)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the store at a fresh sqlite file per test so tests can't see
    each other's data or the real data/trade_state.db."""
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "test_state.db"))


def _fix_today(monkeypatch, date_obj):
    monkeypatch.setattr(circuit_breaker.CircuitBreaker, "_today", lambda self: date_obj)


def _set_equity(monkeypatch, equity):
    monkeypatch.setattr(broker, "get_account_snapshot", lambda: {"equity": equity})


class TestFreshStart:
    def test_first_check_sets_baseline_to_current_equity_and_persists_it(self, monkeypatch):
        _fix_today(monkeypatch, TODAY)
        _set_equity(monkeypatch, 10000.0)

        breaker = circuit_breaker.CircuitBreaker()
        breaker.check_drawdown()

        assert breaker.halted() is False
        assert circuit_breaker_store.load() == {
            "day": str(TODAY), "day_start_equity": 10000.0, "halted": False,
        }


class TestRestartSameDay:
    def test_restart_keeps_original_baseline_not_current_equity(self, monkeypatch):
        _fix_today(monkeypatch, TODAY)

        _set_equity(monkeypatch, 10000.0)
        first = circuit_breaker.CircuitBreaker()
        first.check_drawdown()

        # Equity has dropped, then the process "restarts" -- a brand new
        # CircuitBreaker instance, as happens after a crash/reboot.
        _set_equity(monkeypatch, 9850.0)  # -1.5%, still under the 2% cap
        second = circuit_breaker.CircuitBreaker()
        second.check_drawdown()

        assert second.halted() is False
        # Baseline must still be the ORIGINAL day-start equity, not reset
        # to whatever equity happened to be at restart.
        assert circuit_breaker_store.load()["day_start_equity"] == 10000.0

    def test_restart_does_not_clear_an_active_halt(self, monkeypatch):
        _fix_today(monkeypatch, TODAY)
        monkeypatch.setattr(broker, "get_open_positions", lambda symbol=None: [])
        monkeypatch.setattr(order_execution, "close_position_full", MagicMock())

        _set_equity(monkeypatch, 10000.0)
        first = circuit_breaker.CircuitBreaker()
        first.check_drawdown()

        _set_equity(monkeypatch, 9700.0)  # -3%, breaches the 2% cap
        first.check_drawdown()
        assert first.halted() is True

        # Simulate a restart: fresh in-memory object, same calendar day.
        second = circuit_breaker.CircuitBreaker()
        assert second.halted() is True

        second.check_drawdown()
        assert second.halted() is True  # still halted, no accidental resume


class TestRestartNewDay:
    def test_stale_halt_from_a_past_day_does_not_carry_over(self, monkeypatch):
        _fix_today(monkeypatch, YESTERDAY)
        monkeypatch.setattr(broker, "get_open_positions", lambda symbol=None: [])
        monkeypatch.setattr(order_execution, "close_position_full", MagicMock())

        _set_equity(monkeypatch, 10000.0)
        first = circuit_breaker.CircuitBreaker()
        first.check_drawdown()

        _set_equity(monkeypatch, 9700.0)  # -3%, breaches the 2% cap
        first.check_drawdown()
        assert first.halted() is True

        _fix_today(monkeypatch, TODAY)
        _set_equity(monkeypatch, 9900.0)
        second = circuit_breaker.CircuitBreaker()
        assert second.halted() is False  # yesterday's halt must not carry over

        second.check_drawdown()
        assert second.halted() is False
        persisted = circuit_breaker_store.load()
        assert persisted["day"] == str(TODAY)
        assert persisted["day_start_equity"] == 9900.0


class TestDrawdownTrip:
    def test_trips_and_closes_open_positions_when_cap_breached(self, monkeypatch):
        _fix_today(monkeypatch, TODAY)

        position = MagicMock(ticket=1)
        tick = MagicMock()
        traded_symbol = config.SYMBOLS[0]
        monkeypatch.setattr(
            broker, "get_open_positions",
            lambda symbol=None: [position] if symbol == traded_symbol else [],
        )
        monkeypatch.setattr(broker, "get_current_tick", lambda symbol: tick)
        close_mock = MagicMock()
        monkeypatch.setattr(order_execution, "close_position_full", close_mock)

        _set_equity(monkeypatch, 10000.0)
        breaker = circuit_breaker.CircuitBreaker()
        breaker.check_drawdown()

        _set_equity(monkeypatch, 9799.0)  # -2.01%, breaches the 2% cap
        breaker.check_drawdown()

        assert breaker.halted() is True
        close_mock.assert_called_once_with(position, tick)

    def test_does_not_trip_just_under_the_cap(self, monkeypatch):
        _fix_today(monkeypatch, TODAY)

        _set_equity(monkeypatch, 10000.0)
        breaker = circuit_breaker.CircuitBreaker()
        breaker.check_drawdown()

        _set_equity(monkeypatch, 9801.0)  # -1.99%, just under the 2% cap
        breaker.check_drawdown()

        assert breaker.halted() is False
