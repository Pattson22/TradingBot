import pandas as pd
import pytest

import mtf_trend


def _h4_df(closes, start="2026-01-01"):
    return pd.DataFrame(
        {"close": closes},
        index=pd.date_range(start, periods=len(closes), freq="4h", tz="UTC"),
    )


class TestLatestTrend:
    def test_bullish_when_close_above_ema(self):
        df = _h4_df([1.0, 1.0, 1.0, 1.1])
        assert mtf_trend.latest_trend(df, ema_period=3) == "bullish"

    def test_bearish_when_close_below_ema(self):
        df = _h4_df([1.1, 1.1, 1.1, 1.0])
        assert mtf_trend.latest_trend(df, ema_period=3) == "bearish"

    def test_none_when_not_warmed_up(self):
        df = _h4_df([1.0, 1.0])  # only 2 bars, period=3 needs 3
        assert mtf_trend.latest_trend(df, ema_period=3) is None

    def test_none_on_empty_dataframe(self):
        df = _h4_df([])
        assert mtf_trend.latest_trend(df, ema_period=3) is None


class TestAlignSeries:
    def test_no_h4_bar_has_closed_yet_gives_none(self):
        h4_df = _h4_df([1, 2, 3, 4, 5], start="2026-01-01")  # first bar opens 00:00, closes 04:00
        h1_index = pd.date_range("2026-01-01 00:00", periods=4, freq="h", tz="UTC")  # 00:00..03:00
        result = mtf_trend.align_series(h1_index, h4_df, ema_period=3, mtf_timeframe_name="H4")
        assert result.isna().all()

    def test_trend_becomes_visible_exactly_at_h4_close_time(self):
        h4_df = _h4_df([1, 2, 3])  # period=3 -> ema first valid at bar index 2 (opens 08:00, closes 12:00)
        h1_index = pd.date_range("2026-01-01 11:00", periods=3, freq="h", tz="UTC")  # 11:00,12:00,13:00
        result = mtf_trend.align_series(h1_index, h4_df, ema_period=3, mtf_timeframe_name="H4")
        assert pd.isna(result.iloc[0])  # 11:00 -> H4 bar not closed yet (closes at 12:00)
        assert result.iloc[1] == "bullish"  # 12:00 -> now visible
        assert result.iloc[2] == "bullish"  # 13:00 -> still visible (carried forward)

    def test_direction_matches_close_vs_ema(self):
        h4_df = _h4_df([5, 4, 3, 1])  # declining -> bearish once warmed up
        h1_index = pd.date_range("2026-01-01 16:00", periods=1, freq="h", tz="UTC")
        result = mtf_trend.align_series(h1_index, h4_df, ema_period=3, mtf_timeframe_name="H4")
        assert result.iloc[0] == "bearish"

    def test_output_indexed_exactly_like_input(self):
        h4_df = _h4_df([1, 2, 3, 4])
        h1_index = pd.date_range("2026-01-01 12:00", periods=5, freq="h", tz="UTC")
        result = mtf_trend.align_series(h1_index, h4_df, ema_period=3, mtf_timeframe_name="H4")
        assert list(result.index) == list(h1_index)
