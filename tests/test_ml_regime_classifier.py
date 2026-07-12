import types

import numpy as np
import pandas as pd
import pytest

import indicators
import market_regime
import ml_regime_classifier as mrc


def _fake_config():
    return types.SimpleNamespace(
        RSI_PERIOD=14,
        TREND_FILTER_EMA_PERIOD=20,
        BOLLINGER_PERIOD=20,
        BOLLINGER_STD_DEV=2.0,
        ATR_PERIOD=14,
        VOLATILITY_PERCENTILE_LOOKBACK=50,
        ADX_PERIOD=14,
    )


def _mixed_regime_enriched_df():
    """150 bars of a smooth, strong uptrend followed by 150 bars of flat
    chop -- a real enriched DataFrame (via indicators.compute_all), not
    hand-faked columns, so the classifier is exercised end-to-end against
    genuinely different market shapes."""
    trend_closes = np.linspace(1.00, 1.30, 150)
    rng = np.random.default_rng(7)
    chop_closes = 1.30 + rng.normal(0, 0.0005, 150)
    closes = np.concatenate([trend_closes, chop_closes])  # plain ndarray -- no index to misalign

    df = pd.DataFrame(
        {
            "high": closes + 0.001,
            "low": closes - 0.001,
            "close": closes,
        },
        index=pd.date_range("2026-01-01", periods=len(closes), freq="h", tz="UTC"),
    )
    return indicators.compute_all(df, _fake_config())


class TestBuildLabels:
    def test_labels_only_trending_or_ranging_or_none(self):
        enriched = _mixed_regime_enriched_df()
        labels, threshold = mrc.build_labels(enriched, forward_bars=10)
        valid_values = set(labels.dropna().unique())
        assert valid_values <= {market_regime.TRENDING, market_regime.RANGING}
        assert isinstance(threshold, float)

    def test_tail_rows_are_none_when_forward_window_runs_out(self):
        enriched = _mixed_regime_enriched_df()
        labels, _ = mrc.build_labels(enriched, forward_bars=10)
        assert labels.iloc[-10:].isna().all()

    def test_matches_hand_computed_trend_strength(self):
        df = pd.DataFrame(
            {"close": [1.00, 1.00, 1.00, 1.10], "atr": [0.01] * 4},
            index=pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC"),
        )
        labels, threshold = mrc.build_labels(df, forward_bars=1)
        # row0: |1.00-1.00|/(0.01*1) = 0.0 ; row1: |1.00-1.00|/0.01=0.0 ;
        # row2: |1.10-1.00|/0.01=10.0 ; row3: NaN (no forward bar).
        # median of [0,0,10] = 0.0 -> row2's strength (10.0) > 0.0 -> trending;
        # rows 0/1 (0.0) are NOT > threshold 0.0 -> ranging.
        assert threshold == pytest.approx(0.0)
        assert labels.iloc[0] == market_regime.RANGING
        assert labels.iloc[1] == market_regime.RANGING
        assert labels.iloc[2] == market_regime.TRENDING
        assert pd.isna(labels.iloc[3])


class TestTrain:
    def test_raises_when_too_few_valid_rows(self):
        tiny = _mixed_regime_enriched_df().iloc[:10]
        with pytest.raises(ValueError):
            mrc.train(tiny, forward_bars=5)

    def test_trains_successfully_on_adequate_data(self):
        enriched = _mixed_regime_enriched_df()
        model, threshold = mrc.train(enriched, forward_bars=10)
        assert model is not None
        assert isinstance(threshold, float)

    def test_deterministic_given_fixed_random_state(self):
        enriched = _mixed_regime_enriched_df()
        model_a, _ = mrc.train(enriched, forward_bars=10, random_state=1)
        model_b, _ = mrc.train(enriched, forward_bars=10, random_state=1)
        preds_a = mrc.predict(model_a, enriched)
        preds_b = mrc.predict(model_b, enriched)
        assert preds_a.equals(preds_b)


class TestPredict:
    def test_warmup_rows_are_none(self):
        enriched = _mixed_regime_enriched_df()
        model, _ = mrc.train(enriched, forward_bars=10)
        predictions = mrc.predict(model, enriched)
        assert pd.isna(predictions.iloc[0])

    def test_predictions_are_valid_regime_labels(self):
        enriched = _mixed_regime_enriched_df()
        model, _ = mrc.train(enriched, forward_bars=10)
        predictions = mrc.predict(model, enriched)
        valid_values = set(predictions.dropna().unique())
        assert valid_values <= {market_regime.TRENDING, market_regime.RANGING}

    def test_never_uses_forward_looking_data(self):
        # predict() on a truncated (no-future) slice must still return a
        # prediction for the last row -- proves it isn't reaching past the
        # end of the data the way build_labels() legitimately does.
        enriched = _mixed_regime_enriched_df()
        model, _ = mrc.train(enriched, forward_bars=10)
        truncated = enriched.iloc[:-5]
        predictions = mrc.predict(model, truncated)
        assert predictions.iloc[-1] in (market_regime.TRENDING, market_regime.RANGING)


class TestSaveLoad:
    def test_round_trips_model_and_metadata(self, tmp_path):
        enriched = _mixed_regime_enriched_df()
        model, threshold = mrc.train(enriched, forward_bars=10)
        path = str(tmp_path / "regime_model.joblib")

        mrc.save(model, threshold, forward_bars=10, path=path)
        loaded_model, loaded_threshold, loaded_forward_bars = mrc.load(path)

        assert loaded_threshold == pytest.approx(threshold)
        assert loaded_forward_bars == 10

        original_preds = mrc.predict(model, enriched)
        loaded_preds = mrc.predict(loaded_model, enriched)
        assert original_preds.equals(loaded_preds)

    def test_creates_missing_parent_directory(self, tmp_path):
        enriched = _mixed_regime_enriched_df()
        model, threshold = mrc.train(enriched, forward_bars=10)
        path = str(tmp_path / "nested" / "dir" / "regime_model.joblib")
        mrc.save(model, threshold, forward_bars=10, path=path)
        assert mrc.load(path)[0] is not None
