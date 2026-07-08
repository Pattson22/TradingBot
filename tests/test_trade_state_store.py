import pytest

import config
import trade_state_store as store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the store at a fresh sqlite file per test so tests can't see
    each other's data or the real data/trade_state.db."""
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "test_state.db"))


def _state(**overrides):
    base = {
        "initial_stop_distance": 0.0018,
        "be_applied": False,
        "partial_done": False,
        "extreme_price": 1.1000,
    }
    base.update(overrides)
    return base


class TestLoadAll:
    def test_empty_db_returns_empty_dict(self):
        assert store.load_all() == {}


class TestSaveAndLoad:
    def test_round_trips_a_single_ticket(self):
        store.save(12345, _state())
        loaded = store.load_all()
        assert loaded == {12345: _state()}

    def test_preserves_bool_types_not_ints(self):
        store.save(1, _state(be_applied=True, partial_done=True))
        loaded = store.load_all()[1]
        assert loaded["be_applied"] is True
        assert loaded["partial_done"] is True

    def test_save_overwrites_existing_ticket(self):
        store.save(1, _state(be_applied=False))
        store.save(1, _state(be_applied=True, extreme_price=1.1050))
        loaded = store.load_all()
        assert len(loaded) == 1
        assert loaded[1]["be_applied"] is True
        assert loaded[1]["extreme_price"] == pytest.approx(1.1050)

    def test_multiple_tickets_independent(self):
        store.save(1, _state(extreme_price=1.1000))
        store.save(2, _state(extreme_price=1.2000))
        loaded = store.load_all()
        assert set(loaded) == {1, 2}
        assert loaded[1]["extreme_price"] == pytest.approx(1.1000)
        assert loaded[2]["extreme_price"] == pytest.approx(1.2000)


class TestDeleteMany:
    def test_removes_only_specified_tickets(self):
        store.save(1, _state())
        store.save(2, _state())
        store.save(3, _state())
        store.delete_many([1, 3])
        assert set(store.load_all()) == {2}

    def test_empty_list_is_a_noop(self):
        store.save(1, _state())
        store.delete_many([])
        assert set(store.load_all()) == {1}

    def test_deleting_unknown_ticket_does_not_error(self):
        store.delete_many([999])
        assert store.load_all() == {}
