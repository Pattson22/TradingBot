"""
Backtest CLI: run the live strategy's exact entry/exit rules against
historical MT5 data and report performance stats.

Requires a running, logged-in MT5 terminal (same as live trading) since
historical bars and symbol tick economics are both sourced from it.

Usage:
    python backtest.py --symbol EURUSD --start 2025-01-01 --end 2026-07-01
    python backtest.py --symbol USDJPY --start 2025-01-01 --end 2026-07-01 --balance 5000
"""

import argparse
import os
from datetime import datetime, timezone

import broker
import config
from backtest_data import fetch_historical_bars
from backtest_engine import run_backtest
from backtest_metrics import format_report, summarize
from logger_setup import configure_logging, get_logger

BACKTEST_LOG_FILE = os.path.join(config.LOG_DIR, "backtest.log")

log = get_logger(__name__)


def _parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Backtest the trading bot's strategy against historical MT5 data."
    )
    parser.add_argument("--symbol", default=config.SYMBOLS[0], help="Symbol to backtest (default: first in config.SYMBOLS)")
    parser.add_argument("--start", required=True, type=_parse_date, help="Start date, YYYY-MM-DD (UTC)")
    parser.add_argument("--end", required=True, type=_parse_date, help="End date, YYYY-MM-DD (UTC)")
    parser.add_argument("--balance", type=float, default=10_000.0, help="Starting balance (default: 10000)")
    parser.add_argument("--timeframe", default=config.TIMEFRAME_NAME, help="Timeframe name, e.g. H1 (default: config.TIMEFRAME_NAME)")
    parser.add_argument("--no-cache", action="store_true", help="Ignore any cached historical bars and re-fetch")
    return parser


def main():
    args = _build_parser().parse_args()

    configure_logging(log_file=BACKTEST_LOG_FILE)
    broker.connect()
    try:
        df = fetch_historical_bars(
            args.symbol, args.start, args.end,
            timeframe_name=args.timeframe, use_cache=not args.no_cache,
        )
        symbol_info = broker.get_symbol_info(args.symbol)
        result = run_backtest(df, symbol_info, initial_balance=args.balance)
        stats = summarize(result.trades, result.equity_curve, args.balance)
        print(format_report(args.symbol, args.start, args.end, stats))
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
