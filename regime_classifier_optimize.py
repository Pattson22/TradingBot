"""
Walk-forward comparison: does the ML regime classifier (ml_regime_classifier.py)
actually beat the fixed ADX>25 rule (market_regime.py) at driving
regime-adaptive RSI thresholds, out-of-sample?

Same fold structure as backtest_optimize.walk_forward (see its _fold_bounds,
reused here), but simpler -- no parameter grid search. The point is a
single, direct, apples-to-apples comparison: for each fold, train the ML
classifier on ONLY the in-sample window, predict regimes for the
out-of-sample window, then run run_backtest_on_enriched TWICE on that same
out-of-sample slice -- once with REGIME_CLASSIFIER_METHOD="adx" (today's
live rule), once with "ml" (the freshly-trained classifier's out-of-sample
predictions, attached as an extra `ml_regime` column the same way
`mtf_trend` already is). Both runs share every other setting (RSI/BB/ATR
thresholds, spread, session/volatility filters), isolating regime-
classification method as the only variable, and both compound their own
balance fold-to-fold independently so the two final numbers are directly
comparable.

REGIME_ADAPTIVE_RSI_ENABLED is forced True for both runs -- this compares
"adx-driven regime-adaptive RSI" vs "ml-driven regime-adaptive RSI", not
against the plain non-regime-adaptive baseline (use backtest_optimize.py /
optimize.py to validate the RSI/BB/ATR thresholds themselves).
"""

from __future__ import annotations

import types

import pandas as pd

import config as default_config
import ml_regime_classifier
import mtf_trend
from backtest_engine import run_backtest_on_enriched
from backtest_metrics import summarize
from backtest_optimize import _fold_bounds
from indicators import compute_all
from logger_setup import get_logger

log = get_logger(__name__)


def _with_regime_method(cfg, method):
    """Shallow copy of `cfg` (module or namespace) with REGIME_ADAPTIVE_RSI_ENABLED
    forced True and REGIME_CLASSIFIER_METHOD set to `method` -- same
    vars()-copy trick backtest_optimize._make_cfg uses."""
    base = {k: v for k, v in vars(cfg).items() if k.isupper()}
    base["REGIME_ADAPTIVE_RSI_ENABLED"] = True
    base["REGIME_CLASSIFIER_METHOD"] = method
    return types.SimpleNamespace(**base)


def compare_regime_methods(
    df,
    mtf_df,
    symbol_info,
    in_sample_bars,
    out_sample_bars,
    forward_bars=ml_regime_classifier.DEFAULT_FORWARD_BARS,
    initial_balance=10_000.0,
    step_bars=None,
    cfg=default_config,
):
    """
    Walk-forward comparison of the ADX rule vs the ML classifier for
    regime-adaptive RSI thresholds. Returns (folds, adx_summary, ml_summary):
      - folds: list of per-fold dicts (split_time, out_sample_end,
        n_training_samples, adx_stats, ml_stats -- each *_stats is that
        fold's own out-of-sample backtest_metrics.summarize() result).
      - adx_summary / ml_summary: pooled summarize() across every fold's
        out-of-sample trades and equity, with balance compounded
        fold-to-fold independently per method -- the headline "which one
        actually wins over the whole span" numbers.
    """
    enriched = compute_all(df, cfg)
    enriched["mtf_trend"] = mtf_trend.align_series(
        enriched.index, mtf_df, cfg.MTF_EMA_PERIOD, cfg.MTF_TIMEFRAME_NAME
    )

    fold_bounds = list(_fold_bounds(len(enriched), in_sample_bars, out_sample_bars, step_bars))
    if not fold_bounds:
        raise ValueError("Not enough bars for even one fold at this window size")

    adx_cfg = _with_regime_method(cfg, "adx")
    ml_cfg = _with_regime_method(cfg, "ml")

    folds = []
    adx_balance = ml_balance = initial_balance
    adx_trades, ml_trades = [], []
    adx_equity_parts, ml_equity_parts = [], []

    for in_start, split, out_end in fold_bounds:
        in_sample = enriched.iloc[in_start:split]
        out_sample = enriched.iloc[split:out_end].copy()

        model, n_training_samples = ml_regime_classifier.train(
            in_sample, forward_bars=forward_bars,
            sl_multiplier=cfg.ATR_SL_MULTIPLIER, risk_reward_ratio=cfg.RISK_REWARD_RATIO,
        )
        out_sample["ml_regime"] = ml_regime_classifier.predict(model, out_sample)

        adx_result = run_backtest_on_enriched(out_sample, symbol_info, initial_balance=adx_balance, cfg=adx_cfg)
        ml_result = run_backtest_on_enriched(out_sample, symbol_info, initial_balance=ml_balance, cfg=ml_cfg)

        folds.append(
            {
                "split_time": enriched.index[split],
                "out_sample_end": enriched.index[out_end - 1],
                "n_training_samples": n_training_samples,
                "adx_stats": summarize(adx_result.trades, adx_result.equity_curve, adx_balance),
                "ml_stats": summarize(ml_result.trades, ml_result.equity_curve, ml_balance),
            }
        )

        adx_trades.extend(adx_result.trades)
        ml_trades.extend(ml_result.trades)
        adx_equity_parts.append(adx_result.equity_curve)
        ml_equity_parts.append(ml_result.equity_curve)
        adx_balance = adx_result.final_balance
        ml_balance = ml_result.final_balance

    adx_summary = summarize(adx_trades, pd.concat(adx_equity_parts), initial_balance)
    ml_summary = summarize(ml_trades, pd.concat(ml_equity_parts), initial_balance)

    return folds, adx_summary, ml_summary
