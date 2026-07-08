"""
Bar-by-bar strategy simulator.

Re-evaluates the exact same rules the live bot uses (signals.py's entry
conditions, trade_manager.py's break-even/partial-TP/ATR-trailing exit
tiers, risk_management.py's fixed-fractional sizing) against historical OHLC
bars, so the strategy's historical performance can be measured before
risking anything live.

`trade_manager.py`/`order_execution.py` are NOT reused directly: they are
built around live MT5 position/tick objects and a running broker
connection. This module reimplements the same tier logic against simulated
bars instead, using `config`'s thresholds so behaviour stays in sync with
the live bot's rules.

--------------------------------------------------------------------------
IMPORTANT APPROXIMATIONS (inherent to OHLC-bar backtesting without tick
data — read before trusting the numbers):
--------------------------------------------------------------------------
1. Indicators are computed once, vectorized, over the whole historical
   DataFrame via indicators.compute_all(). This is NOT lookahead bias: every
   indicator here (SMA, Wilder EMA/RSI/ATR, rolling std) is causal — each
   row's value depends only on that row and earlier rows, identical to
   calling it incrementally bar-by-bar, just far faster.
2. Entries fill at the signal bar's own close (matching live: the bot acts
   on the last CLOSED candle and sends the order immediately after, so the
   real fill price is ~= that close, modulo tiny live slippage which isn't
   modeled here).
3. Exit-tier management (BE / partial-TP / trailing) only gets bar-level
   granularity (checked once per bar using that bar's High/Low), whereas the
   live bot polls every `POLL_INTERVAL_SECONDS` using real-time ticks. A
   move that would have triggered a tier intrabar is applied using that
   bar's most favourable price reached — a standard, but optimistic-leaning,
   OHLC backtesting approximation.
4. If both the stop-loss and take-profit fall inside the same bar's
   High-Low range, the stop-loss is assumed to hit first (the conservative,
   non-optimistic tie-break, since we can't know the true intrabar path).
5. Stop/tier checks for a bar use the SL as it stood at the END of the
   previous bar, then tier updates (if the position survives) apply for the
   *next* bar's check — avoiding a same-bar "retroactively saved by its own
   trailing update" lookahead.
6. No slippage, spread, or partial-fill modeling on entries/exits.
7. Multi-timeframe confirmation (mtf_trend.py) is aligned with an as-of
   backward join shifted by the higher timeframe's own bar duration, so a
   not-yet-closed higher-timeframe bar never leaks into an earlier H1 bar's
   signal check — see mtf_trend.align_series's docstring. Like the H1
   EMA200 filter, this warmup cost is paid once at the very start of
   whatever date range is requested (no automatic pre-fetch of extra
   lookback), so it eats a larger fraction of short backtest windows.
"""

import math
from collections import namedtuple

import pandas as pd

import config as default_config
import mtf_trend
import risk_management
from indicators import compute_all
from logger_setup import get_logger

log = get_logger(__name__)

TradeRecord = namedtuple(
    "TradeRecord",
    [
        "direction",
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "initial_volume",
        "total_pnl",
        "r_multiple",
        "be_applied",
        "partial_done",
    ],
)

BacktestResult = namedtuple("BacktestResult", ["trades", "equity_curve", "final_balance"])


class _OpenPosition:
    def __init__(self, direction, entry_time, entry_price, volume, stop_distance, sl, tp,
                 tick_size, tick_value, volume_step, volume_min):
        self.direction = direction
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.initial_volume = volume
        self.remaining_volume = volume
        self.initial_stop_distance = stop_distance
        self.sl = sl
        self.tp = tp
        self.be_applied = False
        self.partial_done = False
        self.extreme_price = entry_price
        self.realized_pnl = 0.0
        self.tick_size = tick_size
        self.tick_value = tick_value
        self.volume_step = volume_step
        self.volume_min = volume_min


def _price_pnl(direction, entry_price, exit_price, volume, tick_size, tick_value):
    price_diff = (exit_price - entry_price) if direction == "buy" else (entry_price - exit_price)
    ticks = price_diff / tick_size
    return ticks * tick_value * volume


def _r_multiple_at(direction, entry_price, price, initial_stop_distance):
    if direction == "buy":
        return (price - entry_price) / initial_stop_distance
    return (entry_price - price) / initial_stop_distance


def _check_entry(row, cfg):
    close = row.close
    bullish_trend = close > row.ema_trend and row.mtf_trend == "bullish"
    bearish_trend = close < row.ema_trend and row.mtf_trend == "bearish"

    if bullish_trend and close <= row.bb_lower and row.rsi < cfg.RSI_OVERSOLD:
        return "buy"
    if bearish_trend and close >= row.bb_upper and row.rsi > cfg.RSI_OVERBOUGHT:
        return "sell"
    return None


def _open_position(direction, row, time, symbol_info, balance, cfg):
    entry_price = row.close
    atr_value = row.atr

    levels = risk_management.calculate_trade_levels(
        direction, entry_price, atr_value, cfg.ATR_SL_MULTIPLIER, cfg.ATR_TP_MULTIPLIER
    )
    lot = risk_management.calculate_lot_size(
        balance=balance,
        risk_per_trade=cfg.RISK_PER_TRADE,
        stop_distance_price=levels.stop_distance,
        tick_size=symbol_info.trade_tick_size,
        tick_value=symbol_info.trade_tick_value,
        volume_step=symbol_info.volume_step,
        volume_min=symbol_info.volume_min,
        volume_max=symbol_info.volume_max,
    )
    if lot is None:
        return None

    return _OpenPosition(
        direction, time, entry_price, lot, levels.stop_distance, levels.stop_loss, levels.take_profit,
        symbol_info.trade_tick_size, symbol_info.trade_tick_value,
        symbol_info.volume_step, symbol_info.volume_min,
    )


def _manage_position(position, row, time, cfg):
    """
    Advance `position` by one bar. Returns (realized_pnl_delta, closed_trade
    or None). `realized_pnl_delta` is the P&L booked to balance this bar
    (from a stop/tp close and/or a partial-TP), zero otherwise.
    """
    direction = position.direction

    if direction == "buy":
        sl_hit = row.low <= position.sl
        tp_hit = row.high >= position.tp
        favourable_price = row.high
    else:
        sl_hit = row.high >= position.sl
        tp_hit = row.low <= position.tp
        favourable_price = row.low

    if sl_hit or tp_hit:
        exit_price = position.sl if sl_hit else position.tp  # SL wins ties, see module docstring
        close_pnl = _price_pnl(
            direction, position.entry_price, exit_price, position.remaining_volume,
            position.tick_size, position.tick_value,
        )
        total_pnl = position.realized_pnl + close_pnl
        risk_amount = (
            position.initial_stop_distance / position.tick_size
        ) * position.tick_value * position.initial_volume
        trade = TradeRecord(
            direction=direction,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=time,
            exit_price=exit_price,
            initial_volume=position.initial_volume,
            total_pnl=total_pnl,
            r_multiple=total_pnl / risk_amount,
            be_applied=position.be_applied,
            partial_done=position.partial_done,
        )
        return close_pnl, trade

    r = _r_multiple_at(direction, position.entry_price, favourable_price, position.initial_stop_distance)
    partial_pnl = 0.0

    if not position.be_applied and r >= cfg.BREAKEVEN_TRIGGER_R:
        position.sl = position.entry_price
        position.be_applied = True

    if not position.partial_done and r >= cfg.PARTIAL_TP_TRIGGER_R:
        raw_partial = position.initial_volume * cfg.PARTIAL_TP_FRACTION
        steps = math.floor(raw_partial / position.volume_step)
        partial_volume = round(steps * position.volume_step, 8)

        if partial_volume >= position.volume_min:
            trigger_price = position.entry_price + (
                position.initial_stop_distance * cfg.PARTIAL_TP_TRIGGER_R
                * (1 if direction == "buy" else -1)
            )
            partial_pnl = _price_pnl(
                direction, position.entry_price, trigger_price, partial_volume,
                position.tick_size, position.tick_value,
            )
            position.realized_pnl += partial_pnl
            position.remaining_volume = round(position.remaining_volume - partial_volume, 8)
        position.partial_done = True

    if position.partial_done:
        if direction == "buy":
            position.extreme_price = max(position.extreme_price, favourable_price)
            trail_sl = position.extreme_price - row.atr * cfg.ATR_TRAIL_MULTIPLIER
            if trail_sl > position.sl:
                position.sl = trail_sl
        else:
            position.extreme_price = min(position.extreme_price, favourable_price)
            trail_sl = position.extreme_price + row.atr * cfg.ATR_TRAIL_MULTIPLIER
            if trail_sl < position.sl:
                position.sl = trail_sl

    return partial_pnl, None


def _unrealized_pnl(position, row):
    return _price_pnl(
        position.direction, position.entry_price, row.close, position.remaining_volume,
        position.tick_size, position.tick_value,
    )


def _warmup_incomplete(row):
    return (
        pd.isna(row.rsi) or pd.isna(row.ema_trend) or pd.isna(row.bb_upper)
        or pd.isna(row.bb_lower) or pd.isna(row.atr) or pd.isna(row.mtf_trend)
    )


def run_backtest(df, mtf_df, symbol_info, initial_balance=10_000.0, cfg=default_config):
    """
    Simulate the live strategy over historical H1 bars `df` and higher-
    timeframe bars `mtf_df` (both as returned by
    backtest_data.fetch_historical_bars, `mtf_df` using
    cfg.MTF_TIMEFRAME_NAME). Only one open position at a time, matching
    main.py's "flat before entry" rule.

    Returns a BacktestResult(trades, equity_curve, final_balance).
    """
    enriched = compute_all(df, cfg)
    enriched["mtf_trend"] = mtf_trend.align_series(
        enriched.index, mtf_df, cfg.MTF_EMA_PERIOD, cfg.MTF_TIMEFRAME_NAME
    )
    return run_backtest_on_enriched(enriched, symbol_info, initial_balance=initial_balance, cfg=cfg)


def run_backtest_on_enriched(enriched, symbol_info, initial_balance=10_000.0, cfg=default_config):
    """
    Same as run_backtest, but takes a DataFrame that already has the
    rsi/ema_trend/bb_upper/bb_lower/atr/mtf_trend columns attached (via
    indicators.compute_all + mtf_trend.align_series). Used by walk-forward
    optimization, which needs to slice pre-computed indicators into folds
    rather than recomputing them fresh on each fold's raw bars —
    recomputing per-fold would cold-restart every rolling/EWM warm-up (e.g.
    EMA200) at the start of each fold, which doesn't reflect a bot that's
    actually been running continuously.
    """
    balance = initial_balance
    position = None
    trades = []
    equity_index = []
    equity_values = []

    for row in enriched.itertuples():
        time = row.Index
        if position is not None:
            pnl_delta, closed_trade = _manage_position(position, row, time, cfg)
            balance += pnl_delta
            if closed_trade is not None:
                trades.append(closed_trade)
                position = None
        elif not _warmup_incomplete(row):
            signal = _check_entry(row, cfg)
            if signal is not None:
                position = _open_position(signal, row, time, symbol_info, balance, cfg)

        mark_to_market = _unrealized_pnl(position, row) if position is not None else 0.0
        equity_index.append(time)
        equity_values.append(balance + mark_to_market)

    equity_curve = pd.Series(equity_values, index=equity_index)
    return BacktestResult(trades=trades, equity_curve=equity_curve, final_balance=balance)
