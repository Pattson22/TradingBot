"""
Central configuration for the trading bot.

Every tunable knob lives here so behaviour can be changed without touching
logic in other modules. Credentials are read from environment variables —
never hardcode account numbers, passwords, or server names in source.
"""

import os

# ---------------------------------------------------------------------------
# MT5 TERMINAL / ACCOUNT CONNECTION
# ---------------------------------------------------------------------------
# Set these as real environment variables before running, e.g.:
#   export MT5_LOGIN=12345678
#   export MT5_PASSWORD="your-password"
#   export MT5_SERVER="YourBroker-Demo"
#   export MT5_PATH="C:\\Program Files\\MetaTrader 5\\terminal64.exe"
MT5_LOGIN = int(os.environ["MT5_LOGIN"]) if os.environ.get("MT5_LOGIN") else None
MT5_PASSWORD = os.environ.get("MT5_PASSWORD")
MT5_SERVER = os.environ.get("MT5_SERVER")
# Optional: explicit path to terminal64.exe. If None, MT5 python package
# tries to auto-discover an already-installed/running terminal.
MT5_PATH = os.environ.get("MT5_PATH") or None

# ---------------------------------------------------------------------------
# SAFETY SWITCHES
# ---------------------------------------------------------------------------
# When True, the bot computes and logs everything it *would* do (signals,
# lot sizes, order requests) but never calls mt5.order_send / order-modifying
# functions. Always start here, especially before your first live/demo run.
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# INSTRUMENTS
# ---------------------------------------------------------------------------
# Symbol names must match exactly what your broker exposes in MT5
# (some brokers suffix pairs, e.g. "EURUSD.a" or "EURUSDm"). Adjust as needed.
# USDJPY dropped 2026-07-08: backtest.py/optimize.py both showed this
# strategy structurally losing money on USDJPY over 18 months of history
# (fixed-default, walk-forward-tuned, and with/without MTF confirmation all
# came back negative — see memory/backtesting_harness.md and
# memory/mtf_confirmation.md), unlike EURUSD which was consistently
# profitable across the same variations.
# AUDUSD added 2026-07-10: same 18-month backtest.py/optimize.py evidence
# process applied to GBPUSD and AUDUSD as candidates. GBPUSD came back
# negative both fixed-default (-0.39%) and walk-forward (-0.58%) -- same
# pattern as USDJPY, left out. AUDUSD came back positive both ways
# (fixed-default +2.99%/10 trades, walk-forward +1.00%/14 trades) --
# added, though note the fixed-default number (70% win rate, zero losing
# trades) is a small, suspiciously clean sample; the walk-forward result
# is the more trustworthy signal here.
SYMBOLS = ["EURUSD", "AUDUSD"]

# Working timeframe for signal generation. Uses MetaTrader5 timeframe
# constants (mt5.TIMEFRAME_H1 etc.) — imported where needed to avoid a
# hard dependency on the MT5 package inside this pure-config module.
TIMEFRAME_NAME = "H1"  # resolved to an mt5.TIMEFRAME_* constant in data_feed.py
BARS_TO_FETCH = 300     # enough history for EMA200 + indicator warm-up

# ---------------------------------------------------------------------------
# STRATEGY PARAMETERS (mean reversion, trend-filtered)
# ---------------------------------------------------------------------------
RSI_PERIOD = 14
# 30/70 (textbook default). 35/65 was tried 2026-07-08 after a walk-forward
# run suggested it — but applied as a fixed default (rather than the
# optimizer's adaptive per-fold choice) it roughly quadrupled trade
# frequency and materially worsened max drawdown on both symbols tested
# (EURUSD -2.18%->-4.13%, USDJPY -8.10%->-13.79%), so reverted. See
# memory/mtf_confirmation.md for the comparison. Re-run optimize.py before
# considering a change here again.
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

BOLLINGER_PERIOD = 20
BOLLINGER_STD_DEV = 2.0

TREND_FILTER_EMA_PERIOD = 200

# Multi-timeframe trend confirmation: entries additionally require this
# higher timeframe's own EMA trend to agree with the H1 EMA200 trend above,
# not just replace it. See mtf_trend.py.
MTF_TIMEFRAME_NAME = "H4"
MTF_EMA_PERIOD = 200
MTF_BARS_TO_FETCH = 300

ATR_PERIOD = 14
ATR_SL_MULTIPLIER = 1.5     # stop-loss distance = ATR * this
ATR_TP_MULTIPLIER = 4.5     # initial safety-net TP distance = ATR * this (~3R)
ATR_TRAIL_MULTIPLIER = 2.0  # trailing stop distance for the runner leg

# ---------------------------------------------------------------------------
# RISK MANAGEMENT
# ---------------------------------------------------------------------------
RISK_PER_TRADE = 0.0025  # 0.25% of current account balance risked per trade

# Multi-tier exit management, expressed in multiples of initial risk (R)
BREAKEVEN_TRIGGER_R = 1.0   # move SL to entry once price reaches +1R
PARTIAL_TP_TRIGGER_R = 2.0  # take partial profit once price reaches +2R
PARTIAL_TP_FRACTION = 0.5   # fraction of position volume closed at that point

# ---------------------------------------------------------------------------
# CIRCUIT BREAKER
# ---------------------------------------------------------------------------
MAX_DAILY_DRAWDOWN_PCT = 0.02  # 2% intraday equity drawdown halts trading

# ---------------------------------------------------------------------------
# SESSION (TIME-OF-DAY) FILTER
# ---------------------------------------------------------------------------
# Opt-in, default OFF: only restrict entries to specific UTC hours (of the
# signal bar's own closed-candle timestamp, not wall-clock "now") once
# backtest.py/optimize.py have shown it actually helps for this strategy —
# don't flip this on as a fixed default without that evidence.
SESSION_FILTER_ENABLED = False
SESSION_ALLOWED_HOURS_UTC = list(range(7, 17))  # London + NY session, placeholder

# ---------------------------------------------------------------------------
# VOLATILITY REGIME FILTER
# ---------------------------------------------------------------------------
# Opt-in, default OFF: skip entries when the current ATR's percentile rank
# within its own trailing history is outside [MIN, MAX] -- too low suggests
# chop with no follow-through, too high suggests news-spike risk. Same
# don't-flip-on-without-backtest-evidence caveat as the session filter above.
VOLATILITY_FILTER_ENABLED = False
VOLATILITY_PERCENTILE_LOOKBACK = 100
VOLATILITY_MIN_PERCENTILE = 0.20
VOLATILITY_MAX_PERCENTILE = 0.80

# ---------------------------------------------------------------------------
# SPREAD FILTER
# ---------------------------------------------------------------------------
# Maximum acceptable spread per symbol, expressed in broker "points"
# (MT5 points, not pips — for a 5-digit EURUSD, 1 pip = 10 points).
# Tune these to each symbol's normal spread; widen only for a considered reason.
MAX_SPREAD_POINTS = {
    "EURUSD": 20,   # ~2.0 pip cap
    "USDJPY": 20,
    "AUDUSD": 20,
}
DEFAULT_MAX_SPREAD_POINTS = 30  # fallback for symbols not listed above

# ---------------------------------------------------------------------------
# ECONOMIC CALENDAR / NEWS FILTER (news_filter.py)
# ---------------------------------------------------------------------------
# Opt-in: only active if NEWS_CALENDAR_URL is set. Left unconfigured by
# default rather than failing safe/blocking-all, since silently blocking
# every trade because a URL wasn't set would be a surprising default for a
# feature nobody asked to enable yet.
#
# Current provider: jblanked.com's Calendar API (see
# https://www.jblanked.com/news/api/docs/calendar/ and
# economic_calendar.transform_jblanked_event). Their free tier is capped at
# 1 request/day, so NEWS_CALENDAR_URL should point at the WEEK endpoint
# (not "today" -- a day-boundary gap would otherwise open up between the
# daily cache refresh and midnight UTC), filtered server-side to High
# impact only, with offset=3 to align their timestamps to UTC, e.g.:
#   https://www.jblanked.com/news/api/mql5/calendar/week/?impact=High&offset=3
# NEWS_CALENDAR_API_KEY is sent as "Authorization: Api-Key <key>" (see
# main.py._build_news_guard) -- get a key from your jblanked.com profile.
NEWS_CALENDAR_URL = os.environ.get("NEWS_CALENDAR_URL") or None
NEWS_CALENDAR_API_KEY = os.environ.get("NEWS_CALENDAR_API_KEY") or None

# How long to reuse a cached calendar fetch before hitting the API again
# (economic_calendar.CachingCalendarProvider). 24h matches jblanked.com's
# free-tier 1 request/day cap -- means an intraday revision to an event's
# scheduled time won't be picked up until the next day's refresh.
NEWS_CALENDAR_CACHE_TTL_SECONDS = 86400

NEWS_RESTRICT_BEFORE_MINUTES = 30
NEWS_RESTRICT_AFTER_MINUTES = 30
NEWS_MAX_ALLOWED_SPREAD_PIPS = 2.5
NEWS_PRE_EVENT_PROTECTION_MINUTES = 5
NEWS_PROTECTION_ACTION = "close"  # "close" or "breakeven"

# ---------------------------------------------------------------------------
# EXECUTION / MISC
# ---------------------------------------------------------------------------
MAGIC_NUMBER = 20260708          # unique id tagging this bot's orders/positions
ORDER_DEVIATION_POINTS = 10      # max allowed slippage on market orders
POLL_INTERVAL_SECONDS = 30       # main loop cadence

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "tradingbot.log")

# ---------------------------------------------------------------------------
# STATE PERSISTENCE
# ---------------------------------------------------------------------------
# Exit-tier state (break-even/partial-TP/trailing progress per open ticket)
# is persisted here so it survives a bot restart. See trade_state_store.py.
DATA_DIR = "data"
STATE_DB_PATH = os.path.join(DATA_DIR, "trade_state.db")
