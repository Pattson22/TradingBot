import pytest

import market_regime


class TestClassify:
    def test_above_threshold_is_trending(self):
        assert market_regime.classify(30.0, trending_threshold=25.0) == market_regime.TRENDING

    def test_at_threshold_is_ranging(self):
        # Strictly greater-than: exactly at the threshold is NOT trending.
        assert market_regime.classify(25.0, trending_threshold=25.0) == market_regime.RANGING

    def test_below_threshold_is_ranging(self):
        assert market_regime.classify(10.0, trending_threshold=25.0) == market_regime.RANGING

    def test_nan_returns_none(self):
        assert market_regime.classify(float("nan"), trending_threshold=25.0) is None
