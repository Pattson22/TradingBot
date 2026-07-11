import pytest

import config
import execution_log_store as store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the store at a fresh sqlite file per test so tests can't see
    each other's data or the real data/trade_state.db."""
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "test_state.db"))


class TestRecord:
    def test_returns_signed_slippage(self):
        slippage = store.record(
            ticket=123, symbol="EURUSD", direction="buy",
            requested_price=1.10000, executed_price=1.10003,
        )
        assert slippage == pytest.approx(0.00003)

    def test_negative_slippage_when_filled_better_than_requested(self):
        slippage = store.record(
            ticket=123, symbol="EURUSD", direction="sell",
            requested_price=1.10000, executed_price=1.09998,
        )
        assert slippage == pytest.approx(-0.00002)

    def test_accepts_a_none_ticket(self):
        # e.g. a DRY_RUN caller wouldn't have a real ticket -- shouldn't be
        # required, though the live order_execution.py path always has one.
        slippage = store.record(
            ticket=None, symbol="EURUSD", direction="buy",
            requested_price=1.10000, executed_price=1.10000,
        )
        assert slippage == pytest.approx(0.0)


class TestRecent:
    def test_empty_store_returns_empty_list(self):
        assert store.recent() == []

    def test_returns_newest_first(self):
        store.record(ticket=1, symbol="EURUSD", direction="buy", requested_price=1.1000, executed_price=1.1001)
        store.record(ticket=2, symbol="AUDUSD", direction="sell", requested_price=0.6500, executed_price=0.6499)
        results = store.recent()
        assert [r["ticket"] for r in results] == [2, 1]

    def test_respects_limit(self):
        for i in range(5):
            store.record(ticket=i, symbol="EURUSD", direction="buy", requested_price=1.1, executed_price=1.1)
        assert len(store.recent(limit=2)) == 2

    def test_round_trips_all_fields(self):
        store.record(ticket=42, symbol="USDCHF", direction="sell", requested_price=0.9000, executed_price=0.9002)
        record = store.recent()[0]
        assert record["ticket"] == 42
        assert record["symbol"] == "USDCHF"
        assert record["direction"] == "sell"
        assert record["requested_price"] == pytest.approx(0.9000)
        assert record["executed_price"] == pytest.approx(0.9002)
        assert record["slippage"] == pytest.approx(0.0002)
        assert record["timestamp"] > 0
