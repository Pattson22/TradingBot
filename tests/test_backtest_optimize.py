import types

import pandas as pd
import pytest

import backtest_optimize as bo
from backtest_engine import BacktestResult, TradeRecord


def _fake_config():
    return types.SimpleNamespace(
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


def _symbol_info():
    return types.SimpleNamespace(
        trade_tick_size=0.00001,
        trade_tick_value=1.0,
        volume_step=0.01,
        volume_min=0.01,
        volume_max=100.0,
    )


def _trade(total_pnl):
    return TradeRecord(
        direction="buy",
        entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
        entry_price=1.1,
        exit_time=pd.Timestamp("2026-01-02", tz="UTC"),
        exit_price=1.11,
        initial_volume=1.0,
        total_pnl=total_pnl,
        r_multiple=total_pnl / 100.0,
        be_applied=False,
        partial_done=False,
    )


class TestMakeCfg:
    def test_keeps_rsi_thresholds_symmetric(self):
        cfg = bo._make_cfg({"RSI_OVERSOLD": 20}, base_cfg=_fake_config())
        assert cfg.RSI_OVERSOLD == 20
        assert cfg.RSI_OVERBOUGHT == 80

    def test_copies_through_unswept_attributes(self):
        cfg = bo._make_cfg({"RSI_OVERSOLD": 25}, base_cfg=_fake_config())
        assert cfg.ATR_TRAIL_MULTIPLIER == 2.0
        assert cfg.PARTIAL_TP_FRACTION == 0.5


class TestGenerateParamGrid:
    def test_produces_full_cartesian_product(self):
        grid = {"RSI_OVERSOLD": [25, 30], "BOLLINGER_STD_DEV": [1.5, 2.0]}
        combos = list(bo.generate_param_grid(grid, base_cfg=_fake_config()))
        assert len(combos) == 4

    def test_combos_are_distinct(self):
        grid = {"RSI_OVERSOLD": [25, 30, 35], "BOLLINGER_STD_DEV": [1.5, 2.0]}
        combos = list(bo.generate_param_grid(grid, base_cfg=_fake_config()))
        seen = {(c.RSI_OVERSOLD, c.BOLLINGER_STD_DEV) for c in combos}
        assert len(seen) == 6


class TestFoldBounds:
    def test_rolls_forward_by_step_bars(self):
        bounds = list(bo._fold_bounds(n_bars=100, in_sample_bars=30, out_sample_bars=10))
        assert bounds[0] == (0, 30, 40)
        assert bounds[1] == (10, 40, 50)
        assert bounds[-1] == (60, 90, 100)
        assert len(bounds) == 7

    def test_no_folds_when_data_too_short(self):
        assert list(bo._fold_bounds(n_bars=20, in_sample_bars=30, out_sample_bars=10)) == []

    def test_custom_step_bars(self):
        bounds = list(bo._fold_bounds(n_bars=100, in_sample_bars=30, out_sample_bars=10, step_bars=20))
        assert bounds[0] == (0, 30, 40)
        assert bounds[1] == (20, 50, 60)


class TestScore:
    def test_total_pnl_sums_trade_pnls(self):
        result = BacktestResult(trades=[_trade(50), _trade(-20)], equity_curve=pd.Series([1.0]), final_balance=0)
        assert bo._score(result, "total_pnl") == pytest.approx(30)

    def test_total_pnl_zero_for_no_trades(self):
        result = BacktestResult(trades=[], equity_curve=pd.Series([1.0]), final_balance=0)
        assert bo._score(result, "total_pnl") == 0

    def test_profit_factor_matches_gross_ratio(self):
        result = BacktestResult(trades=[_trade(50), _trade(-20)], equity_curve=pd.Series([1.0, 1.05]), final_balance=0)
        assert bo._score(result, "profit_factor") == pytest.approx(2.5)

    def test_unknown_objective_raises(self):
        result = BacktestResult(trades=[], equity_curve=pd.Series([1.0]), final_balance=0)
        with pytest.raises(ValueError):
            bo._score(result, "not_a_real_objective")


class TestWalkForwardSmoke:
    def test_no_signals_yields_empty_folds_and_flat_balance(self):
        n = 200
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
        tiny_grid = {
            "RSI_OVERSOLD": [30],
            "BOLLINGER_STD_DEV": [2.0],
            "ATR_SL_MULTIPLIER": [1.5],
            "ATR_TP_MULTIPLIER": [4.5],
        }

        mtf_df = pd.DataFrame(
            {"close": [1.1000] * 60},
            index=pd.date_range("2025-12-01", periods=60, freq="4h", tz="UTC"),
        )

        folds, oos_trades, final_balance = bo.walk_forward(
            df, mtf_df, _symbol_info(),
            in_sample_bars=100, out_sample_bars=50,
            grid=tiny_grid, initial_balance=10_000.0,
            base_cfg=_fake_config(),
        )

        assert len(folds) == 2
        assert oos_trades == []
        assert final_balance == pytest.approx(10_000.0)
        for fold in folds:
            assert fold["out_of_sample_stats"]["num_trades"] == 0
