import types

import numpy as np
import pandas as pd
import pytest

import regime_classifier_optimize as rco


def _cfg(**overrides):
    base = dict(
        RSI_PERIOD=14,
        # Deliberately much slower than BOLLINGER_PERIOD (matching
        # production's real 200-vs-20 gap) -- see
        # test_ml_regime_classifier.py's _fake_config for why: if these
        # track each other too closely, the "close > ema_trend AND close
        # <= bb_lower" setup becomes nearly impossible to ever satisfy.
        TREND_FILTER_EMA_PERIOD=100,
        BOLLINGER_PERIOD=20,
        BOLLINGER_STD_DEV=2.0,
        ATR_PERIOD=14,
        RSI_OVERSOLD=30,
        RSI_OVERBOUGHT=70,
        ATR_SL_MULTIPLIER=1.5,
        RISK_REWARD_RATIO=3.0,
        RISK_PER_TRADE=0.01,
        BREAKEVEN_TRIGGER_R=1.0,
        PARTIAL_TP_TRIGGER_R=2.0,
        PARTIAL_TP_FRACTION=0.5,
        ATR_TRAIL_MULTIPLIER=2.0,
        MTF_TIMEFRAME_NAME="H4",
        MTF_EMA_PERIOD=20,
        SESSION_FILTER_ENABLED=False,
        SESSION_ALLOWED_HOURS_UTC=list(range(24)),
        VOLATILITY_FILTER_ENABLED=False,
        VOLATILITY_PERCENTILE_LOOKBACK=50,
        VOLATILITY_MIN_PERCENTILE=0.20,
        VOLATILITY_MAX_PERCENTILE=0.80,
        MAX_SPREAD_POINTS={},
        DEFAULT_MAX_SPREAD_POINTS=30,
        ADX_PERIOD=14,
        ADX_TRENDING_THRESHOLD=25,
        RSI_OVERSOLD_TRENDING=40,
        RSI_OVERBOUGHT_TRENDING=60,
        RSI_OVERSOLD_RANGING=25,
        RSI_OVERBOUGHT_RANGING=75,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _symbol_info():
    return types.SimpleNamespace(
        name="EURUSD",
        trade_tick_size=0.00001,
        trade_tick_value=1.0,
        volume_step=0.01,
        volume_min=0.01,
        volume_max=100.0,
    )


class TestCompareRegimeMethods:
    def test_runs_end_to_end_and_produces_comparable_stats(self):
        # Needs to be large enough that real trend+Bollinger setup bars
        # occur often enough for ml_regime_classifier.train() to clear
        # MIN_TRAINING_SAMPLES within a single in-sample fold. Note: raising
        # the noise sigma does NOT raise the setup rate -- Bollinger bands
        # are self-normalizing (calibrated to +/-2 std of their own
        # trailing volatility), so touch frequency for a pure random walk
        # is scale-invariant; only MORE BARS increases the count. (A
        # literally flat series also makes ADX mathematically undefined --
        # zero directional movement forever -> 0/0 in the +DI/-DI ratio, a
        # genuine NaN, not a bug -- so some noise is still needed regardless.)
        n = 8000
        rng = np.random.default_rng(11)
        closes = 1.1000 + np.cumsum(rng.normal(0, 0.00022, n))
        df = pd.DataFrame(
            {
                "high": closes + np.abs(rng.normal(0, 0.00008, n)),
                "low": closes - np.abs(rng.normal(0, 0.00008, n)),
                "close": closes,
                "spread": [10] * n,
            },
            index=pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"),
        )
        mtf_df = pd.DataFrame(
            {"close": 1.1000 + np.cumsum(rng.normal(0, 0.00022, 2100))},
            index=pd.date_range("2023-11-15", periods=2100, freq="4h", tz="UTC"),
        )

        folds, adx_summary, ml_summary = rco.compare_regime_methods(
            df, mtf_df, _symbol_info(),
            in_sample_bars=6000, out_sample_bars=1000,
            forward_bars=24, initial_balance=10_000.0, cfg=_cfg(),
        )

        assert len(folds) >= 1
        for fold in folds:
            assert isinstance(fold["n_training_samples"], int)
            for stats in (fold["adx_stats"], fold["ml_stats"]):
                assert stats["num_trades"] >= 0
                assert isinstance(stats["final_balance"], float)

        for summary in (adx_summary, ml_summary):
            assert summary["num_trades"] >= 0
            assert isinstance(summary["final_balance"], float)

    def test_raises_when_not_enough_bars_for_one_fold(self):
        n = 50
        closes = [1.1000] * n
        df = pd.DataFrame(
            {
                "high": [c + 0.0001 for c in closes],
                "low": [c - 0.0001 for c in closes],
                "close": closes,
                "spread": [10] * n,
            },
            index=pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            dtype=float,
        )
        mtf_df = pd.DataFrame(
            {"close": [1.1000] * 20},
            index=pd.date_range("2025-12-28", periods=20, freq="4h", tz="UTC"),
        )

        with pytest.raises(ValueError):
            rco.compare_regime_methods(
                df, mtf_df, _symbol_info(),
                in_sample_bars=200, out_sample_bars=100,
                cfg=_cfg(),
            )

    def test_adx_and_ml_methods_are_isolated_from_each_other(self):
        # Forcing REGIME_ADAPTIVE_RSI_ENABLED/REGIME_CLASSIFIER_METHOD in
        # _with_regime_method must not leak back into the caller's own cfg.
        cfg = _cfg()
        rco._with_regime_method(cfg, "ml")
        assert not hasattr(cfg, "REGIME_ADAPTIVE_RSI_ENABLED")

        adx_cfg = rco._with_regime_method(cfg, "adx")
        ml_cfg = rco._with_regime_method(cfg, "ml")
        assert adx_cfg.REGIME_CLASSIFIER_METHOD == "adx"
        assert ml_cfg.REGIME_CLASSIFIER_METHOD == "ml"
        assert adx_cfg.REGIME_ADAPTIVE_RSI_ENABLED is True
        assert ml_cfg.REGIME_ADAPTIVE_RSI_ENABLED is True
