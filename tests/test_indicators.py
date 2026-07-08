import types

import numpy as np
import pandas as pd
import pytest

import indicators


class TestSma:
    def test_rolling_mean_with_warmup_nans(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = indicators.sma(s, period=3)
        assert result.iloc[:2].isna().all()
        assert result.iloc[2:].tolist() == pytest.approx([2.0, 3.0, 4.0])


class TestEma:
    def test_matches_hand_computed_recursion(self):
        # span=3 -> alpha=2/(3+1)=0.5, adjust=False recursion seeded at x0.
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        result = indicators.ema(s, period=3)
        assert result.iloc[:2].isna().all()
        expected = [2.25, 3.125, 4.0625, 5.03125]
        assert result.iloc[2:].tolist() == pytest.approx(expected)


class TestRsi:
    def test_all_gains_gives_rsi_100_after_warmup(self):
        closes = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        result = indicators.rsi(closes, period=3)
        assert result.iloc[:3].isna().all()
        assert result.iloc[3:].tolist() == pytest.approx([100.0] * 4)

    def test_all_losses_gives_rsi_0_after_warmup(self):
        closes = pd.Series([7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0])
        result = indicators.rsi(closes, period=3)
        assert result.iloc[:3].isna().all()
        assert result.iloc[3:].tolist() == pytest.approx([0.0] * 4)

    def test_bounded_between_0_and_100_on_mixed_data(self):
        rng = np.random.default_rng(seed=42)
        closes = pd.Series(100 + np.cumsum(rng.normal(0, 1, size=200)))
        result = indicators.rsi(closes, period=14)
        valid = result.dropna()
        assert not valid.empty
        assert (valid >= 0).all() and (valid <= 100).all()


class TestBollingerBands:
    def test_matches_hand_computed_population_std(self):
        closes = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        middle, upper, lower = indicators.bollinger_bands(closes, period=3, std_dev=2.0)

        assert middle.iloc[:2].isna().all()
        assert middle.iloc[2:].tolist() == pytest.approx([2.0, 3.0, 4.0])

        pop_std = (2.0 / 3.0) ** 0.5  # population std of any 3 consecutive integers
        assert upper.iloc[2:].tolist() == pytest.approx([2.0 + 2 * pop_std, 3.0 + 2 * pop_std, 4.0 + 2 * pop_std])
        assert lower.iloc[2:].tolist() == pytest.approx([2.0 - 2 * pop_std, 3.0 - 2 * pop_std, 4.0 - 2 * pop_std])


class TestAtr:
    def test_constant_true_range_yields_constant_atr(self):
        df = pd.DataFrame(
            {
                "high": [10.0, 11.0, 12.0],
                "low": [8.0, 9.0, 10.0],
                "close": [9.0, 10.0, 11.0],
            }
        )
        result = indicators.atr(df, period=2)
        assert result.iloc[0:1].isna().all()
        assert result.iloc[1:].tolist() == pytest.approx([2.0, 2.0])

    def test_gap_widens_true_range_beyond_high_low(self):
        # A gap-up open where prev close is far below today's low should make
        # true range = high - prev_close, not just high - low.
        df = pd.DataFrame(
            {
                "high": [10.0, 20.0],
                "low": [8.0, 18.0],
                "close": [9.0, 19.0],
            }
        )
        result = indicators.atr(df, period=1)
        # true_range[1] = max(20-18, |20-9|, |18-9|) = max(2, 11, 9) = 11
        assert result.iloc[1] == pytest.approx(11.0)


class TestComputeAll:
    def _fake_config(self):
        return types.SimpleNamespace(
            RSI_PERIOD=3,
            TREND_FILTER_EMA_PERIOD=3,
            BOLLINGER_PERIOD=3,
            BOLLINGER_STD_DEV=2.0,
            ATR_PERIOD=3,
        )

    def test_attaches_all_expected_columns(self):
        closes = list(range(1, 11))
        df = pd.DataFrame(
            {
                "high": [c + 1 for c in closes],
                "low": [c - 1 for c in closes],
                "close": closes,
            },
            dtype=float,
        )
        result = indicators.compute_all(df, self._fake_config())

        for col in ("rsi", "ema_trend", "bb_upper", "bb_lower", "atr"):
            assert col in result.columns

        # Warm-up rows are NaN, later rows are fully populated.
        assert result[["rsi", "ema_trend", "bb_upper", "bb_lower", "atr"]].iloc[0].isna().all()
        assert not result[["rsi", "ema_trend", "bb_upper", "bb_lower", "atr"]].iloc[-1].isna().any()

    def test_does_not_mutate_input_dataframe(self):
        df = pd.DataFrame(
            {"high": [2.0, 3.0, 4.0], "low": [0.0, 1.0, 2.0], "close": [1.0, 2.0, 3.0]}
        )
        original_columns = list(df.columns)
        indicators.compute_all(df, self._fake_config())
        assert list(df.columns) == original_columns
