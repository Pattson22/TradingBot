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
        # Deliberately much slower than BOLLINGER_PERIOD, matching
        # production's real gap (200 vs 20) -- if these track each other
        # too closely, "close > ema_trend AND close <= bb_lower" becomes
        # nearly impossible (bb_lower sits below a similarly-paced moving
        # average by construction), and the setup essentially never fires.
        TREND_FILTER_EMA_PERIOD=100,
        BOLLINGER_PERIOD=20,
        BOLLINGER_STD_DEV=2.0,
        ATR_PERIOD=14,
        VOLATILITY_PERCENTILE_LOOKBACK=50,
        ADX_PERIOD=14,
    )


def _mixed_regime_enriched_df(n=5000, seed=11):
    """A noisy random walk, long enough and volatile enough that
    trend+Bollinger setup bars (the only bars build_labels can actually
    label) occur often enough to clear MIN_TRAINING_SAMPLES. mtf_trend is a
    synthetic higher-timeframe proxy (price vs. a slower moving average)
    so trend alignment isn't trivially always-true or always-false."""
    rng = np.random.default_rng(seed)
    closes = 1.1000 + np.cumsum(rng.normal(0, 0.00015, n))
    highs = closes + np.abs(rng.normal(0, 0.00008, n))
    lows = closes - np.abs(rng.normal(0, 0.00008, n))

    df = pd.DataFrame(
        {"high": highs, "low": lows, "close": closes},
        index=pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"),
    )
    enriched = indicators.compute_all(df, _fake_config())

    slow_ma = enriched["close"].rolling(80, min_periods=1).mean()
    enriched["mtf_trend"] = np.where(enriched["close"] > slow_ma, "bullish", "bearish")
    return enriched


def _hand_row(**overrides):
    base = dict(
        close=1.0940, high=1.0945, low=1.0935, ema_trend=1.0900,
        bb_lower=1.0950, bb_upper=1.1100, atr=0.0010, mtf_trend="bullish",
    )
    base.update(overrides)
    return base


def _hand_df(rows):
    return pd.DataFrame(rows, index=pd.date_range("2026-01-01", periods=len(rows), freq="h", tz="UTC"))


class TestBuildLabels:
    def test_winning_setup_is_labeled_trending(self):
        # Row0 qualifies as a buy setup (close<=bb_lower, uptrend context,
        # bullish mtf). SL=1.0925, TP=1.0985 (atr=0.0010, sl_mult=1.5,
        # rr=3.0). Take-profit is reached on the 3rd forward bar.
        df = _hand_df([
            _hand_row(),
            _hand_row(close=1.0950, high=1.0955, low=1.0945),
            _hand_row(close=1.0960, high=1.0965, low=1.0955),
            _hand_row(close=1.1010, high=1.1015, low=1.1005),  # high >= TP 1.0985
        ])
        labels = mrc.build_labels(df, forward_bars=3, sl_multiplier=1.5, risk_reward_ratio=3.0)
        assert labels.iloc[0] == market_regime.TRENDING

    def test_losing_setup_is_labeled_ranging(self):
        # Same setup/levels, but the very next bar's low breaches the
        # 1.0925 stop-loss before ever approaching take-profit.
        df = _hand_df([
            _hand_row(),
            _hand_row(close=1.0910, high=1.0915, low=1.0910),  # low <= SL 1.0925
            _hand_row(),
            _hand_row(),
        ])
        labels = mrc.build_labels(df, forward_bars=3, sl_multiplier=1.5, risk_reward_ratio=3.0)
        assert labels.iloc[0] == market_regime.RANGING

    def test_same_bar_sl_and_tp_breach_favors_stop_loss(self):
        df = _hand_df([
            _hand_row(),
            _hand_row(close=1.1000, high=1.1500, low=1.0500),  # engulfs both SL and TP
        ])
        labels = mrc.build_labels(df, forward_bars=1, sl_multiplier=1.5, risk_reward_ratio=3.0)
        assert labels.iloc[0] == market_regime.RANGING

    def test_unresolved_within_window_is_labeled_ranging(self):
        df = _hand_df([
            _hand_row(),
            _hand_row(close=1.0945, high=1.0950, low=1.0940),  # neither level touched
        ])
        labels = mrc.build_labels(df, forward_bars=1, sl_multiplier=1.5, risk_reward_ratio=3.0)
        assert labels.iloc[0] == market_regime.RANGING

    def test_no_qualifying_setup_is_none(self):
        # mtf_trend disagrees with the H1 uptrend context -> no setup at all.
        df = _hand_df([
            _hand_row(mtf_trend="bearish"),
            _hand_row(),
            _hand_row(),
        ])
        labels = mrc.build_labels(df, forward_bars=1, sl_multiplier=1.5, risk_reward_ratio=3.0)
        assert pd.isna(labels.iloc[0])

    def test_tail_rows_with_no_room_to_resolve_are_none(self):
        df = _mixed_regime_enriched_df(n=500)
        labels = mrc.build_labels(df, forward_bars=24, sl_multiplier=1.5, risk_reward_ratio=3.0)
        assert labels.iloc[-24:].isna().all()

    def test_labels_only_trending_ranging_or_none(self):
        df = _mixed_regime_enriched_df(n=1500)
        labels = mrc.build_labels(df, forward_bars=24, sl_multiplier=1.5, risk_reward_ratio=3.0)
        valid_values = set(labels.dropna().unique())
        assert valid_values <= {market_regime.TRENDING, market_regime.RANGING}


class TestTrain:
    def test_raises_when_too_few_valid_rows(self):
        tiny = _mixed_regime_enriched_df(n=60)
        with pytest.raises(ValueError):
            mrc.train(tiny, forward_bars=24)

    def test_trains_successfully_on_adequate_data(self):
        enriched = _mixed_regime_enriched_df()
        model, n_training_samples = mrc.train(enriched, forward_bars=24)
        assert model is not None
        assert n_training_samples >= mrc.MIN_TRAINING_SAMPLES

    def test_deterministic_given_fixed_random_state(self):
        enriched = _mixed_regime_enriched_df()
        model_a, _ = mrc.train(enriched, forward_bars=24, random_state=1)
        model_b, _ = mrc.train(enriched, forward_bars=24, random_state=1)
        preds_a = mrc.predict(model_a, enriched)
        preds_b = mrc.predict(model_b, enriched)
        assert preds_a.equals(preds_b)


class TestPredict:
    def test_warmup_rows_are_none(self):
        enriched = _mixed_regime_enriched_df()
        model, _ = mrc.train(enriched, forward_bars=24)
        predictions = mrc.predict(model, enriched)
        assert pd.isna(predictions.iloc[0])

    def test_predictions_are_valid_regime_labels(self):
        enriched = _mixed_regime_enriched_df()
        model, _ = mrc.train(enriched, forward_bars=24)
        predictions = mrc.predict(model, enriched)
        valid_values = set(predictions.dropna().unique())
        assert valid_values <= {market_regime.TRENDING, market_regime.RANGING}

    def test_never_uses_forward_looking_data(self):
        # predict() on a truncated (no-future) slice must still return a
        # prediction for the last row -- proves it isn't reaching past the
        # end of the data the way build_labels() legitimately does.
        enriched = _mixed_regime_enriched_df()
        model, _ = mrc.train(enriched, forward_bars=24)
        truncated = enriched.iloc[:-5]
        predictions = mrc.predict(model, truncated)
        assert predictions.iloc[-1] in (market_regime.TRENDING, market_regime.RANGING)


class TestSaveLoad:
    def test_round_trips_model_and_metadata(self, tmp_path):
        enriched = _mixed_regime_enriched_df()
        model, _ = mrc.train(enriched, forward_bars=24, sl_multiplier=1.5, risk_reward_ratio=3.0)
        path = str(tmp_path / "regime_model.joblib")

        mrc.save(model, forward_bars=24, sl_multiplier=1.5, risk_reward_ratio=3.0, path=path)
        loaded_model, loaded_forward_bars, loaded_sl, loaded_rr = mrc.load(path)

        assert loaded_forward_bars == 24
        assert loaded_sl == pytest.approx(1.5)
        assert loaded_rr == pytest.approx(3.0)

        original_preds = mrc.predict(model, enriched)
        loaded_preds = mrc.predict(loaded_model, enriched)
        assert original_preds.equals(loaded_preds)

    def test_creates_missing_parent_directory(self, tmp_path):
        enriched = _mixed_regime_enriched_df()
        model, _ = mrc.train(enriched, forward_bars=24)
        path = str(tmp_path / "nested" / "dir" / "regime_model.joblib")
        mrc.save(model, forward_bars=24, sl_multiplier=1.5, risk_reward_ratio=3.0, path=path)
        assert mrc.load(path)[0] is not None
