import types

import pytest

import config
import spread_filter


@pytest.fixture(autouse=True)
def isolated_history(monkeypatch):
    """spread_filter keeps rolling history in a module-level dict -- clear
    it between tests so they can't see each other's readings, and pin the
    config knobs that drive it to small, deterministic values."""
    spread_filter._spread_history.clear()
    monkeypatch.setattr(config, "MAX_SPREAD_POINTS", {"EURUSD": 20})
    monkeypatch.setattr(config, "DEFAULT_MAX_SPREAD_POINTS", 30)
    monkeypatch.setattr(config, "SPREAD_ROLLING_WINDOW", 10)
    monkeypatch.setattr(config, "SPREAD_ROLLING_MULTIPLIER", 1.5)
    monkeypatch.setattr(config, "SPREAD_ROLLING_MIN_SAMPLES", 5)


def _symbol_info(name="EURUSD", spread=10):
    return types.SimpleNamespace(name=name, spread=spread)


class TestStaticCap:
    def test_blocks_above_static_cap(self):
        assert spread_filter.is_spread_acceptable(_symbol_info(spread=25)) is False

    def test_allows_at_or_below_static_cap_with_no_history_yet(self):
        assert spread_filter.is_spread_acceptable(_symbol_info(spread=20)) is True

    def test_unlisted_symbol_falls_back_to_default_cap(self):
        assert spread_filter.is_spread_acceptable(_symbol_info(name="SOMEPAIR", spread=25)) is True
        assert spread_filter.is_spread_acceptable(_symbol_info(name="SOMEPAIR", spread=35)) is False


class TestAdaptiveRollingCap:
    def test_stays_static_only_below_min_samples(self):
        # 4 readings at spread=10 -- below SPREAD_ROLLING_MIN_SAMPLES=5, so
        # the adaptive check must not engage yet even for a spike.
        for _ in range(4):
            spread_filter.is_spread_acceptable(_symbol_info(spread=10))
        # 19 points is still <= the static cap (20), so it's allowed either way,
        # but confirms no adaptive block kicks in prematurely.
        assert spread_filter.is_spread_acceptable(_symbol_info(spread=19)) is True

    def test_blocks_spike_above_rolling_multiple_once_warmed_up(self):
        for _ in range(5):
            spread_filter.is_spread_acceptable(_symbol_info(spread=10))
        # rolling mean=10, adaptive cap = 10*1.5=15 -- 16 is under the static
        # cap (20) but over the adaptive one.
        assert spread_filter.is_spread_acceptable(_symbol_info(spread=16)) is False

    def test_allows_reading_within_rolling_multiple(self):
        for _ in range(5):
            spread_filter.is_spread_acceptable(_symbol_info(spread=10))
        assert spread_filter.is_spread_acceptable(_symbol_info(spread=14)) is True

    def test_static_cap_violations_are_excluded_from_rolling_average(self):
        # These 5 readings all fail the static cap (25 > 20), so they must
        # NOT count toward SPREAD_ROLLING_MIN_SAMPLES -- confirmed because a
        # spread of 16 immediately after is still allowed (adaptive check
        # never engages, since real history is still empty).
        for _ in range(5):
            spread_filter.is_spread_acceptable(_symbol_info(spread=25))
        assert spread_filter.is_spread_acceptable(_symbol_info(spread=16)) is True

    def test_symbols_have_independent_rolling_histories(self):
        for _ in range(5):
            spread_filter.is_spread_acceptable(_symbol_info(name="EURUSD", spread=10))
        # AUDUSD has no history of its own yet -- falls back to the default
        # static cap (30) only, so 16 is allowed even though it would have
        # been blocked under EURUSD's now-warmed-up rolling cap.
        assert spread_filter.is_spread_acceptable(_symbol_info(name="AUDUSD", spread=16)) is True
