"""
Walk-forward parameter optimization CLI.

Requires a running, logged-in MT5 terminal, same as backtest.py.

Usage:
    python optimize.py --symbol EURUSD --start 2024-01-01 --end 2026-07-01
    python optimize.py --symbol USDJPY --start 2024-01-01 --end 2026-07-01 \
        --in-sample-bars 3000 --out-sample-bars 750 --objective profit_factor
"""

import argparse
import os
from datetime import datetime, timezone

import broker
import config
from backtest_data import fetch_historical_bars
from backtest_optimize import walk_forward
from logger_setup import configure_logging, get_logger

OPTIMIZE_LOG_FILE = os.path.join(config.LOG_DIR, "optimize.log")
log = get_logger(__name__)


def _parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Walk-forward parameter optimization against historical MT5 data."
    )
    parser.add_argument("--symbol", default=config.SYMBOLS[0])
    parser.add_argument("--start", required=True, type=_parse_date, help="YYYY-MM-DD (UTC)")
    parser.add_argument("--end", required=True, type=_parse_date, help="YYYY-MM-DD (UTC)")
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--timeframe", default=config.TIMEFRAME_NAME)
    parser.add_argument("--in-sample-bars", type=int, default=3000, help="Bars used to pick each fold's parameters")
    parser.add_argument("--out-sample-bars", type=int, default=750, help="Bars used to measure each fold's chosen parameters")
    parser.add_argument("--objective", default="total_pnl", choices=["total_pnl", "profit_factor"])
    parser.add_argument("--no-cache", action="store_true")
    return parser


def _print_report(symbol, start, end, in_sample_bars, out_sample_bars, folds, oos_trades, initial_balance, final_balance):
    print(f"Walk-forward optimization: {symbol} | {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    print(f"{len(folds)} fold(s) | in-sample={in_sample_bars} bars, out-of-sample={out_sample_bars} bars")
    print("-" * 78)

    if not folds:
        print("Not enough historical bars for even one fold at this window size.")
        return

    for i, fold in enumerate(folds, 1):
        p = fold["chosen_params"]
        s = fold["out_of_sample_stats"]
        win_rate = f"{s['win_rate'] * 100:.0f}%" if s["win_rate"] is not None else "n/a"
        print(
            f"Fold {i}: {fold['split_time']:%Y-%m-%d} -> {fold['out_sample_end']:%Y-%m-%d} | "
            f"RSI {p['RSI_OVERSOLD']}/{p['RSI_OVERBOUGHT']} BB{p['BOLLINGER_STD_DEV']} "
            f"SL x{p['ATR_SL_MULTIPLIER']} RR 1:{p['RISK_REWARD_RATIO']} -> "
            f"{s['num_trades']} trades, win rate {win_rate}, return {s['total_return_pct']:+.2f}%"
        )

    print("-" * 78)
    total_return_pct = (final_balance - initial_balance) / initial_balance * 100
    print(f"Combined out-of-sample trades: {len(oos_trades)}")
    print(f"Final balance (compounded fold-to-fold): {final_balance:.2f}")
    print(f"Total out-of-sample return: {total_return_pct:+.2f}%")


def main():
    args = _build_parser().parse_args()

    configure_logging(log_file=OPTIMIZE_LOG_FILE)
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
        folds, oos_trades, final_balance = walk_forward(
            df, mtf_df, symbol_info,
            in_sample_bars=args.in_sample_bars,
            out_sample_bars=args.out_sample_bars,
            initial_balance=args.balance,
            objective=args.objective,
        )
        _print_report(
            args.symbol, args.start, args.end, args.in_sample_bars, args.out_sample_bars,
            folds, oos_trades, args.balance, final_balance,
        )
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
