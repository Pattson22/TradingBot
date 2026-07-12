"""
Walk-forward comparison CLI: does the ML regime classifier beat the plain
ADX>25 rule for regime-adaptive RSI thresholds, out-of-sample?

Requires a running, logged-in MT5 terminal, same as backtest.py/optimize.py.
This does NOT change any live behaviour by itself -- REGIME_CLASSIFIER_METHOD
stays "adx" in config.py regardless of what this reports. Use the result to
decide whether wiring the ML path into signals.py is worth doing at all.

Usage:
    python compare_regime_classifier.py --symbol EURUSD --start 2024-01-01 --end 2026-07-01
    python compare_regime_classifier.py --symbol EURUSD --start 2024-01-01 --end 2026-07-01 \
        --in-sample-bars 3000 --out-sample-bars 750 --forward-bars 24
"""

import argparse
import os
from datetime import datetime, timezone

import broker
import config
import ml_regime_classifier
from backtest_data import fetch_historical_bars
from logger_setup import configure_logging, get_logger
from regime_classifier_optimize import compare_regime_methods

COMPARE_LOG_FILE = os.path.join(config.LOG_DIR, "compare_regime_classifier.log")
log = get_logger(__name__)


def _parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Walk-forward comparison of the ADX rule vs. the ML regime classifier."
    )
    parser.add_argument("--symbol", default=config.SYMBOLS[0])
    parser.add_argument("--start", required=True, type=_parse_date, help="YYYY-MM-DD (UTC)")
    parser.add_argument("--end", required=True, type=_parse_date, help="YYYY-MM-DD (UTC)")
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--timeframe", default=config.TIMEFRAME_NAME)
    parser.add_argument("--in-sample-bars", type=int, default=3000, help="Bars used to train each fold's classifier")
    parser.add_argument("--out-sample-bars", type=int, default=750, help="Bars used to measure each fold OOS")
    parser.add_argument(
        "--forward-bars", type=int, default=ml_regime_classifier.DEFAULT_FORWARD_BARS,
        help="Forward window used to build training labels (see ml_regime_classifier.build_labels)",
    )
    parser.add_argument("--no-cache", action="store_true")
    return parser


def _fmt_stats(stats):
    if stats["num_trades"] == 0:
        return "0 trades"
    win_rate = f"{stats['win_rate'] * 100:.0f}%"
    return f"{stats['num_trades']} trades, win rate {win_rate}, return {stats['total_return_pct']:+.2f}%"


def _print_report(symbol, start, end, folds, adx_summary, ml_summary):
    print(f"Regime classifier comparison: {symbol} | {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    print(f"{len(folds)} fold(s)")
    print("-" * 78)

    for i, fold in enumerate(folds, 1):
        print(f"Fold {i}: {fold['split_time']:%Y-%m-%d} -> {fold['out_sample_end']:%Y-%m-%d}")
        print(f"    ADX rule: {_fmt_stats(fold['adx_stats'])}")
        print(f"    ML model: {_fmt_stats(fold['ml_stats'])} (trained on {fold['n_training_samples']} labeled setup bars)")

    print("-" * 78)
    print(f"ADX rule overall: {_fmt_stats(adx_summary)}")
    print(f"ML model overall: {_fmt_stats(ml_summary)}")
    print("-" * 78)
    diff = ml_summary["total_return_pct"] - adx_summary["total_return_pct"]
    verdict = "ML model wins" if diff > 0 else ("ADX rule wins" if diff < 0 else "Tied")
    print(f"{verdict}: ML {diff:+.2f} percentage points vs ADX, over the combined out-of-sample span.")
    print("REGIME_CLASSIFIER_METHOD in config.py is unchanged by this report -- decide manually.")


def main():
    args = _build_parser().parse_args()

    configure_logging(log_file=COMPARE_LOG_FILE)
    broker.connect()
    try:
        df = fetch_historical_bars(
            args.symbol, args.start, args.end,
            timeframe_name=args.timeframe, use_cache=not args.no_cache,
        )
        mtf_df = fetch_historical_bars(
            args.symbol, args.start, args.end,
            timeframe_name=config.MTF_TIMEFRAME_NAME, use_cache=not args.no_cache,
        )
        symbol_info = broker.get_symbol_info(args.symbol)
        folds, adx_summary, ml_summary = compare_regime_methods(
            df, mtf_df, symbol_info,
            in_sample_bars=args.in_sample_bars,
            out_sample_bars=args.out_sample_bars,
            forward_bars=args.forward_bars,
            initial_balance=args.balance,
        )
        _print_report(args.symbol, args.start, args.end, folds, adx_summary, ml_summary)
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
