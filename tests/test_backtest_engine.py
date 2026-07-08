import types

import pandas as pd
import pytest

import backtest_engine as be


def _cfg(**overrides):
    base = dict(
        RSI_PERIOD=3,
        TREND_FILTER_EMA_PERIOD=3,
        BOLLINGER_PERIOD=3,
        BOLLINGER_STD_DEV=2.0,
        ATR_PERIOD=3,
        RSI_OVERSOLD=30,
        RSI_OVERBOUGHT=70,
        ATR_SL_MULTIPLIER=1.5,
        ATR_TP_MULTIPLIER=4.5,
        RISK_PER_TRADE=0.01,
        BREAKEVEN_TRIGGER_R=1.0,
        PARTIAL_TP_TRIGGER_R=2.0,
        PARTIAL_TP_FRACTION=0.5,
        ATR_TRAIL_MULTIPLIER=2.0,
        MTF_TIMEFRAME_NAME="H4",
        MTF_EMA_PERIOD=3,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _symbol_info(**overrides):
    base = dict(
        trade_tick_size=0.00001,
        trade_tick_value=1.0,
        volume_step=0.01,
        volume_min=0.01,
        volume_max=100.0,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _row(**fields):
    return pd.Series(fields, name=pd.Timestamp("2026-01-01", tz="UTC"))


class TestPriceHelpers:
    def test_buy_pnl_positive_when_price_rises(self):
        pnl = be._price_pnl("buy", 1.1000, 1.1010, 1.0, 0.00001, 1.0)
        assert pnl == pytest.approx((0.0010 / 0.00001) * 1.0 * 1.0)

    def test_sell_pnl_positive_when_price_falls(self):
        pnl = be._price_pnl("sell", 1.1000, 1.0990, 1.0, 0.00001, 1.0)
        assert pnl == pytest.approx((0.0010 / 0.00001) * 1.0 * 1.0)

    def test_r_multiple_buy_and_sell(self):
        assert be._r_multiple_at("buy", 1.1000, 1.1015, 0.0015) == pytest.approx(1.0)
        assert be._r_multiple_at("sell", 1.1000, 1.0985, 0.0015) == pytest.approx(1.0)


class TestCheckEntry:
    def test_buy_signal_when_all_conditions_met(self):
        row = _row(close=1.0940, ema_trend=1.0900, bb_lower=1.0950, bb_upper=1.1100, rsi=25, mtf_trend="bullish")
        assert be._check_entry(row, _cfg()) == "buy"

    def test_sell_signal_when_all_conditions_met(self):
        row = _row(close=1.1060, ema_trend=1.1100, bb_lower=1.0900, bb_upper=1.1050, rsi=75, mtf_trend="bearish")
        assert be._check_entry(row, _cfg()) == "sell"

    def test_no_signal_when_trend_disagrees(self):
        # Bearish trend but price at the lower band -> no long (fighting the trend).
        row = _row(close=1.0940, ema_trend=1.1100, bb_lower=1.0950, bb_upper=1.1200, rsi=25, mtf_trend="bearish")
        assert be._check_entry(row, _cfg()) is None

    def test_no_signal_when_rsi_not_extreme(self):
        row = _row(close=1.0940, ema_trend=1.0900, bb_lower=1.0950, bb_upper=1.1100, rsi=50, mtf_trend="bullish")
        assert be._check_entry(row, _cfg()) is None

    def test_no_signal_when_higher_timeframe_disagrees(self):
        # H1 trend/BB/RSI all say "buy", but the higher timeframe is bearish.
        row = _row(close=1.0940, ema_trend=1.0900, bb_lower=1.0950, bb_upper=1.1100, rsi=25, mtf_trend="bearish")
        assert be._check_entry(row, _cfg()) is None

    def test_no_signal_when_higher_timeframe_not_warmed_up(self):
        row = _row(close=1.0940, ema_trend=1.0900, bb_lower=1.0950, bb_upper=1.1100, rsi=25, mtf_trend=None)
        assert be._check_entry(row, _cfg()) is None


class TestOpenPosition:
    def test_computes_expected_levels_and_lot(self):
        row = _row(close=1.1000, atr=0.0010)
        position = be._open_position("buy", row, row.name, _symbol_info(), balance=10_000.0, cfg=_cfg())
        assert position.entry_price == pytest.approx(1.1000)
        assert position.initial_stop_distance == pytest.approx(0.0015)
        assert position.sl == pytest.approx(1.0985)
        assert position.tp == pytest.approx(1.1045)
        assert position.initial_volume == pytest.approx(0.66)

    def test_returns_none_when_lot_below_minimum(self):
        row = _row(close=1.1000, atr=0.0010)
        position = be._open_position("buy", row, row.name, _symbol_info(), balance=1.0, cfg=_cfg())
        assert position is None


class TestManagePosition:
    def _fresh_buy_position(self):
        row = _row(close=1.1000, atr=0.0010)
        return be._open_position("buy", row, row.name, _symbol_info(), balance=10_000.0, cfg=_cfg())

    def test_stop_loss_hit_closes_at_minus_1r(self):
        position = self._fresh_buy_position()
        bar = _row(high=1.1000, low=1.0980, close=1.0985, atr=0.0010)
        pnl_delta, trade = be._manage_position(position, bar, bar.name, _cfg())
        assert trade is not None
        assert trade.exit_price == pytest.approx(1.0985)
        assert trade.r_multiple == pytest.approx(-1.0)
        assert pnl_delta == pytest.approx(trade.total_pnl)

    def test_take_profit_hit_closes_at_plus_3r(self):
        position = self._fresh_buy_position()
        bar = _row(high=1.1045, low=1.1000, close=1.1040, atr=0.0010)
        pnl_delta, trade = be._manage_position(position, bar, bar.name, _cfg())
        assert trade is not None
        assert trade.exit_price == pytest.approx(1.1045)
        # ATR_TP_MULTIPLIER/ATR_SL_MULTIPLIER = 4.5/1.5 = 3R by construction.
        assert trade.r_multiple == pytest.approx(3.0)

    def test_both_sl_and_tp_in_range_ties_to_stop_loss(self):
        position = self._fresh_buy_position()
        bar = _row(high=1.1050, low=1.0980, close=1.1000, atr=0.0010)
        pnl_delta, trade = be._manage_position(position, bar, bar.name, _cfg())
        assert trade is not None
        assert trade.exit_price == pytest.approx(position.sl)
        assert trade.r_multiple == pytest.approx(-1.0)

    def test_breakeven_moves_stop_without_closing(self):
        position = self._fresh_buy_position()
        bar = _row(high=1.10151, low=1.0995, close=1.1010, atr=0.0010)
        pnl_delta, trade = be._manage_position(position, bar, bar.name, _cfg())
        assert trade is None
        assert pnl_delta == pytest.approx(0.0)
        assert position.be_applied is True
        assert position.sl == pytest.approx(1.1000)
        assert position.partial_done is False

    def test_partial_tp_realizes_pnl_and_engages_trailing(self):
        position = self._fresh_buy_position()
        bar = _row(high=1.10301, low=1.0995, close=1.1020, atr=0.0010)
        pnl_delta, trade = be._manage_position(position, bar, bar.name, _cfg())

        assert trade is None
        assert position.be_applied is True
        assert position.partial_done is True
        assert position.remaining_volume == pytest.approx(0.33)
        # partial_volume=0.33 closed at the +2R price (1.1030): (0.0030/0.00001)*1.0*0.33
        expected_partial_pnl = (0.0030 / 0.00001) * 1.0 * 0.33
        assert pnl_delta == pytest.approx(expected_partial_pnl)
        assert position.realized_pnl == pytest.approx(expected_partial_pnl)
        # Trailing stop (extreme=1.10301, atr=0.0010, mult=2.0) = 1.10101, tighter than BE's 1.1000.
        assert position.sl == pytest.approx(1.10101)

    def test_trailing_stop_only_tightens_never_loosens(self):
        position = self._fresh_buy_position()
        bar1 = _row(high=1.10301, low=1.0995, close=1.1020, atr=0.0010)
        be._manage_position(position, bar1, bar1.name, _cfg())
        sl_after_first_trail = position.sl

        # Price pulls back (lower high) -> extreme_price shouldn't regress, so
        # the trailing stop must not loosen even though this bar's high is lower.
        bar2 = _row(high=1.1020, low=1.1005, close=1.1010, atr=0.0010)
        be._manage_position(position, bar2, bar2.name, _cfg())
        assert position.sl == pytest.approx(sl_after_first_trail)


class TestRunBacktestSmoke:
    def _fake_config(self):
        return _cfg()

    def test_no_signals_leaves_balance_and_equity_flat(self):
        # Flat, low-volatility prices near a stable mean should never satisfy
        # the BB+RSI extreme conditions, so no trade should ever open.
        n = 30
        closes = [1.1000] * n
        df = pd.DataFrame(
            {
                "high": [c + 0.0001 for c in closes],
                "low": [c - 0.0001 for c in closes],
                "close": closes,
            },
            index=pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            dtype=float,
        )

        mtf_df = pd.DataFrame(
            {"close": [1.1000] * 10},
            index=pd.date_range("2025-12-30", periods=10, freq="4h", tz="UTC"),
        )

        result = be.run_backtest(df, mtf_df, _symbol_info(), initial_balance=10_000.0, cfg=self._fake_config())

        assert result.trades == []
        assert result.final_balance == pytest.approx(10_000.0)
        assert len(result.equity_curve) == n
        assert (result.equity_curve - 10_000.0).abs().max() < 1e-6
