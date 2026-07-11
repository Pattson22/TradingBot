import pytest

import circuit_breaker_store as store
import config


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the store at a fresh sqlite file per test so tests can't see
    each other's data or the real data/trade_state.db."""
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "test_state.db"))


class TestLoad:
    def test_returns_none_when_nothing_persisted(self):
        assert store.load() is None


class TestSaveAndLoad:
    def test_round_trips_state(self):
        store.save("2026-07-11", 10000.0, False)
        assert store.load() == {"day": "2026-07-11", "day_start_equity": 10000.0, "halted": False}

    def test_preserves_halted_true(self):
        store.save("2026-07-11", 9800.0, True)
        loaded = store.load()
        assert loaded["halted"] is True

    def test_save_overwrites_previous_state(self):
        store.save("2026-07-11", 10000.0, False)
        store.save("2026-07-12", 9950.0, True)
        assert store.load() == {"day": "2026-07-12", "day_start_equity": 9950.0, "halted": True}
