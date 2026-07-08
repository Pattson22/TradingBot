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
