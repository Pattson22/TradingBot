import pandas as pd
import pytest

import regime_features


def _enriched_df():
    return pd.DataFrame(
        {
            "close": [1.10, 1.11, 1.12, 1.13, 1.14, 1.15],
            "ema_trend": [1.00, 1.02, 1.04, 1.06, 1.08, 1.10],
            "bb_upper": [1.20, 1.20, 1.20, 1.20, 1.20, 1.20],
            "bb_lower": [1.00, 1.00, 1.00, 1.00, 1.00, 1.00],
            "rsi": [50.0, 55.0, 60.0, 65.0, 70.0, 75.0],
            "atr": [0.01, 0.01, 0.01, 0.01, 0.01, 0.01],
            "atr_percentile": [0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
            "adx": [20.0, 22.0, 24.0, 26.0, 28.0, 30.0],
        }
    )


class TestBuildFeatures:
    def test_returns_exactly_feature_columns(self):
        result = regime_features.build_features(_enriched_df())
        assert list(result.columns) == regime_features.FEATURE_COLUMNS

    def test_reuses_existing_indicator_columns_unchanged(self):
        result = regime_features.build_features(_enriched_df())
        assert result["adx"].tolist() == [20.0, 22.0, 24.0, 26.0, 28.0, 30.0]
        assert result["rsi"].tolist() == [50.0, 55.0, 60.0, 65.0, 70.0, 75.0]
        assert result["atr_percentile"].tolist() == [0.5] * 6

    def test_bb_width_is_band_range_normalized_by_price(self):
        result = regime_features.build_features(_enriched_df())
        # (1.20 - 1.00) / close
        expected = [0.20 / c for c in [1.10, 1.11, 1.12, 1.13, 1.14, 1.15]]
        assert result["bb_width"].tolist() == pytest.approx(expected)

    def test_close_to_ema_atr_measures_stretch_in_atr_units(self):
        result = regime_features.build_features(_enriched_df())
        # (close - ema_trend) / atr, e.g. row0: (1.10-1.00)/0.01 = 10.0
        assert result["close_to_ema_atr"].iloc[0] == pytest.approx(10.0)
        assert result["close_to_ema_atr"].iloc[-1] == pytest.approx((1.15 - 1.10) / 0.01)

    def test_ema_slope_matches_hand_computation(self):
        result = regime_features.build_features(_enriched_df(), ema_slope_lookback=5)
        # Only row index 5 has a valid lookback=5 shift within a 6-row frame.
        assert result["ema_slope"].iloc[:5].isna().all()
        # ema_trend[5]=1.10, ema_trend[0]=1.00 -> (1.10-1.00)/(0.01*5) = 2.0
        assert result["ema_slope"].iloc[5] == pytest.approx(2.0)

    def test_does_not_mutate_input_dataframe(self):
        df = _enriched_df()
        original_columns = list(df.columns)
        regime_features.build_features(df)
        assert list(df.columns) == original_columns

    def test_warmup_nan_propagates_from_atr(self):
        df = _enriched_df()
        df.loc[0, "atr"] = float("nan")
        result = regime_features.build_features(df)
        assert pd.isna(result["ema_slope"].iloc[0])
        assert pd.isna(result["close_to_ema_atr"].iloc[0])
