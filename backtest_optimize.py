"""
Walk-forward parameter optimization.

Sweeps RSI/Bollinger/ATR threshold parameters across sequential in-sample /
out-of-sample folds: for each fold, the best-scoring combo is chosen using
ONLY the in-sample window, then that combo's performance is measured on the
following out-of-sample window. Only out-of-sample results are reported.

This is the standard guard against simply grid-searching the entire
historical dataset and reporting whichever combo happened to fit it best —
that number would be close to meaningless (overfit to noise in that specific
history) rather than a signal about likely future performance. Walk-forward
instead asks "if you could only have picked parameters using data available
at the time, how would that choice have performed on data you hadn't seen
yet" — repeated across multiple rolling folds.

Only the *threshold* parameters are swept (RSI_OVERSOLD/OVERBOUGHT kept
symmetric around 50, BOLLINGER_STD_DEV, ATR_SL_MULTIPLIER, RISK_REWARD_RATIO)
— not period lengths (RSI_PERIOD, BOLLINGER_PERIOD, ATR_PERIOD,
TREND_FILTER_EMA_PERIOD stay fixed at config.py's values). BOLLINGER_STD_DEV
is the only swept parameter that feeds indicator computation itself (it
changes the band width), so indicators are computed once per distinct
std-dev value and reused across every other combo that shares it, rather
than recomputed per combo.

RISK_REWARD_RATIO replaced the old ATR_TP_MULTIPLIER (see risk_management.
calculate_trade_levels): sweeping the ratio directly instead of an
independent TP multiplier means every (SL, RR) cell explores a genuinely
distinct risk:reward shape, instead of the old grid's redundancy (e.g. old
SL=1.0/TP=3.0 and SL=1.5/TP=4.5 both happened to be the same 3R ratio at
different absolute distances).
"""

import itertools
import types

import config as default_config
import mtf_trend
from backtest_engine import run_backtest_on_enriched
from backtest_metrics import summarize
from indicators import compute_all
from logger_setup import get_logger

log = get_logger(__name__)

DEFAULT_GRID = {
    "RSI_OVERSOLD": [25, 30, 35],
    "BOLLINGER_STD_DEV": [1.5, 2.0, 2.5],
    "ATR_SL_MULTIPLIER": [1.0, 1.5, 2.0],
    "RISK_REWARD_RATIO": [2.0, 3.0, 4.0],
}


def _make_cfg(overrides, base_cfg=default_config):
    base = {k: v for k, v in vars(base_cfg).items() if k.isupper()}
    base.update(overrides)
    base["RSI_OVERBOUGHT"] = 100 - base["RSI_OVERSOLD"]
    return types.SimpleNamespace(**base)


def generate_param_grid(grid=None, base_cfg=default_config):
    """Yield one config-like namespace per point in the Cartesian product
    of `grid` (default DEFAULT_GRID), with every other config.py value
    copied through unchanged from `base_cfg`."""
    grid = grid or DEFAULT_GRID
    keys = list(grid)
    for values in itertools.product(*(grid[k] for k in keys)):
        yield _make_cfg(dict(zip(keys, values)), base_cfg=base_cfg)


def _fold_bounds(n_bars, in_sample_bars, out_sample_bars, step_bars=None):
    """Yield (in_start, split, out_end) index triples for sequential,
    non-lookahead folds. Rolls forward by `step_bars` (default:
    out_sample_bars, i.e. non-overlapping out-of-sample windows)."""
    step_bars = step_bars or out_sample_bars
    start = 0
    while start + in_sample_bars + out_sample_bars <= n_bars:
        yield start, start + in_sample_bars, start + in_sample_bars + out_sample_bars
        start += step_bars


def _score(result, objective):
    if objective == "total_pnl":
        return sum(t.total_pnl for t in result.trades)
    if objective == "profit_factor":
        stats = summarize(result.trades, result.equity_curve, initial_balance=1.0)
        pf = stats["profit_factor"]
        return pf if pf is not None else float("-inf")
    raise ValueError(f"Unknown objective: {objective!r}")


def walk_forward(
    df,
    mtf_df,
    symbol_info,
    in_sample_bars,
    out_sample_bars,
    grid=None,
    initial_balance=10_000.0,
    objective="total_pnl",
    step_bars=None,
    base_cfg=default_config,
):
    """
    Run walk-forward optimization over `df` (H1 bars) confirmed against
    `mtf_df` (higher-timeframe bars, cfg.MTF_TIMEFRAME_NAME). Returns
    (folds, out_of_sample_trades, final_balance):
      - folds: list of dicts, one per fold, with the chosen params and that
        fold's out-of-sample stats (see backtest_metrics.summarize).
      - out_of_sample_trades: every fold's out-of-sample TradeRecords,
        concatenated in time order.
      - final_balance: starting balance compounded through every fold's
        out-of-sample result only (never touched by in-sample selection).
    """
    combos = list(generate_param_grid(grid, base_cfg=base_cfg))
    if not combos:
        raise ValueError("Parameter grid produced no combinations")

    std_dev_groups = {}
    for cfg in combos:
        std_dev_groups.setdefault(cfg.BOLLINGER_STD_DEV, []).append(cfg)

    enriched_by_std_dev = {}
    for std_dev, cfg_list in std_dev_groups.items():
        enriched = compute_all(df, cfg_list[0])
        enriched["mtf_trend"] = mtf_trend.align_series(
            enriched.index, mtf_df, cfg_list[0].MTF_EMA_PERIOD, cfg_list[0].MTF_TIMEFRAME_NAME
        )
        enriched_by_std_dev[std_dev] = enriched

    folds = []
    out_of_sample_trades = []
    balance = initial_balance

    for in_start, split, out_end in _fold_bounds(len(df), in_sample_bars, out_sample_bars, step_bars):
        best_cfg, best_score = None, None
        for cfg in combos:
            enriched = enriched_by_std_dev[cfg.BOLLINGER_STD_DEV]
            in_sample = enriched.iloc[in_start:split]
            result = run_backtest_on_enriched(in_sample, symbol_info, initial_balance=balance, cfg=cfg)
            score = _score(result, objective)
            if best_score is None or score > best_score:
                best_score, best_cfg = score, cfg

        any_enriched = enriched_by_std_dev[best_cfg.BOLLINGER_STD_DEV]
        out_sample = any_enriched.iloc[split:out_end]
        out_result = run_backtest_on_enriched(out_sample, symbol_info, initial_balance=balance, cfg=best_cfg)
        stats = summarize(out_result.trades, out_result.equity_curve, balance)

        folds.append(
            {
                "in_sample_start": any_enriched.index[in_start],
                "split_time": any_enriched.index[split],
                "out_sample_end": any_enriched.index[out_end - 1],
                "chosen_params": {
                    "RSI_OVERSOLD": best_cfg.RSI_OVERSOLD,
                    "RSI_OVERBOUGHT": best_cfg.RSI_OVERBOUGHT,
                    "BOLLINGER_STD_DEV": best_cfg.BOLLINGER_STD_DEV,
                    "ATR_SL_MULTIPLIER": best_cfg.ATR_SL_MULTIPLIER,
                    "RISK_REWARD_RATIO": best_cfg.RISK_REWARD_RATIO,
                },
                "out_of_sample_stats": stats,
            }
        )
        out_of_sample_trades.extend(out_result.trades)
        balance = out_result.final_balance

    return folds, out_of_sample_trades, balance
