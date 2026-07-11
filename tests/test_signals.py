import pandas as pd
import pytest

import signals


@pytest.fixture(autouse=True)
def small_mtf_config(monkeypatch):
    # signals.py reads config.* as a global, not an injectable parameter, so
    # monkeypatch the handful of fields generate() actually touches. Only
    # MTF_EMA_PERIOD needs shrinking (to keep mtf fixtures tiny); the
    # RSI/BB/EMA thresholds stay at their real defaults since compute_all()
    # is stubbed out in every test below.
    monkeypatch.setattr(signals.config, "MTF_EMA_PERIOD", 3)
    monkeypatch.setattr(signals.config, "MTF_TIMEFRAME_NAME", "H4")


def _enriched_row(**overrides):
    base = dict(close=1.0940, ema_trend=1.0900, bb_lower=1.0950, bb_upper=1.1100, rsi=25, atr=0.0010)
    base.update(overrides)
    return pd.DataFrame([base], index=[pd.Timestamp("2026-01-01", tz="UTC")])


def _enable_regime_filter(monkeypatch):
    monkeypatch.setattr(signals.config, "REGIME_ADAPTIVE_RSI_ENABLED", True)
    monkeypatch.setattr(signals.config, "ADX_TRENDING_THRESHOLD", 25)
    monkeypatch.setattr(signals.config, "RSI_OVERSOLD_TRENDING", 40)
    monkeypatch.setattr(signals.config, "RSI_OVERBOUGHT_TRENDING", 60)
    monkeypatch.setattr(signals.config, "RSI_OVERSOLD_RANGING", 25)
    monkeypatch.setattr(signals.config, "RSI_OVERBOUGHT_RANGING", 75)


def _mtf_df(closes):
    return pd.DataFrame(
        {"close": closes},
        index=pd.date_range("2026-01-01", periods=len(closes), freq="4h", tz="UTC"),
    )


_DUMMY_H1_DF = pd.DataFrame({"close": [1.0940]})
_BULLISH_MTF = _mtf_df([1.0, 1.0, 1.0, 1.1])  # rising -> bullish
_BEARISH_MTF = _mtf_df([1.1, 1.1, 1.1, 1.0])  # falling -> bearish


class TestGenerate:
    def test_buy_signal_when_h1_and_mtf_both_bullish(self, monkeypatch):
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row())
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is not None
        assert result.direction == "buy"

    def test_sell_signal_when_h1_and_mtf_both_bearish(self, monkeypatch):
        row = _enriched_row(close=1.1060, ema_trend=1.1100, bb_lower=1.0900, bb_upper=1.1050, rsi=75)
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: row)
        result = signals.generate(_DUMMY_H1_DF, _BEARISH_MTF)
        assert result is not None
        assert result.direction == "sell"

    def test_no_signal_when_mtf_disagrees_with_bullish_h1_setup(self, monkeypatch):
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row())
        result = signals.generate(_DUMMY_H1_DF, _BEARISH_MTF)
        assert result is None

    def test_no_signal_when_mtf_not_warmed_up(self, monkeypatch):
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row())
        result = signals.generate(_DUMMY_H1_DF, _mtf_df([1.0, 1.0]))  # only 2 bars, period=3
        assert result is None

    def test_no_signal_when_h1_indicators_not_warmed_up(self, monkeypatch):
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row(rsi=float("nan")))
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is None

    def test_no_signal_when_rsi_not_extreme(self, monkeypatch):
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row(rsi=50))
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is None

    def test_session_filter_disabled_by_default_allows_any_hour(self, monkeypatch):
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row())
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is not None

    def test_session_filter_blocks_entry_outside_allowed_hours(self, monkeypatch):
        # Fixture row's timestamp (2026-01-01) has hour=0.
        monkeypatch.setattr(signals.config, "SESSION_FILTER_ENABLED", True)
        monkeypatch.setattr(signals.config, "SESSION_ALLOWED_HOURS_UTC", list(range(7, 17)))
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row())
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is None

    def test_session_filter_allows_entry_inside_allowed_hours(self, monkeypatch):
        monkeypatch.setattr(signals.config, "SESSION_FILTER_ENABLED", True)
        monkeypatch.setattr(signals.config, "SESSION_ALLOWED_HOURS_UTC", [0])
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row())
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is not None

    def test_volatility_filter_disabled_by_default_ignores_atr_percentile(self, monkeypatch):
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row())  # no atr_percentile field
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is not None

    def test_volatility_filter_blocks_entry_outside_percentile_range(self, monkeypatch):
        monkeypatch.setattr(signals.config, "VOLATILITY_FILTER_ENABLED", True)
        monkeypatch.setattr(signals.config, "VOLATILITY_MIN_PERCENTILE", 0.20)
        monkeypatch.setattr(signals.config, "VOLATILITY_MAX_PERCENTILE", 0.80)
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row(atr_percentile=0.05))
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is None

    def test_volatility_filter_blocks_entry_when_atr_percentile_nan(self, monkeypatch):
        monkeypatch.setattr(signals.config, "VOLATILITY_FILTER_ENABLED", True)
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row(atr_percentile=float("nan")))
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is None

    def test_volatility_filter_allows_entry_inside_percentile_range(self, monkeypatch):
        monkeypatch.setattr(signals.config, "VOLATILITY_FILTER_ENABLED", True)
        monkeypatch.setattr(signals.config, "VOLATILITY_MIN_PERCENTILE", 0.20)
        monkeypatch.setattr(signals.config, "VOLATILITY_MAX_PERCENTILE", 0.80)
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row(atr_percentile=0.5))
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is not None


class TestRegimeAdaptiveRsi:
    def test_disabled_by_default_ignores_adx_entirely(self, monkeypatch):
        # No "adx" field on the row at all -- must not be touched when disabled.
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row(rsi=25))
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is not None

    def test_blocks_entry_when_adx_not_warmed_up(self, monkeypatch):
        _enable_regime_filter(monkeypatch)
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row(rsi=25, adx=float("nan")))
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is None

    def test_trending_regime_allows_shallower_rsi_pullback(self, monkeypatch):
        _enable_regime_filter(monkeypatch)
        # rsi=38: below TRENDING's oversold (40) but NOT below RANGING's (25) --
        # only fires because ADX classifies this bar as trending.
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row(rsi=38, adx=30))
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is not None
        assert "regime=trending" in result.reason

    def test_ranging_regime_requires_deeper_rsi_extreme(self, monkeypatch):
        _enable_regime_filter(monkeypatch)
        # rsi=28 would satisfy the plain default RSI_OVERSOLD=30, but RANGING's
        # stricter threshold (25) must block it to avoid trading chop.
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row(rsi=28, adx=10))
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is None

    def test_ranging_regime_still_fires_on_a_deep_enough_extreme(self, monkeypatch):
        _enable_regime_filter(monkeypatch)
        monkeypatch.setattr(signals, "compute_all", lambda df, cfg: _enriched_row(rsi=20, adx=10))
        result = signals.generate(_DUMMY_H1_DF, _BULLISH_MTF)
        assert result is not None
        assert "regime=ranging" in result.reason
