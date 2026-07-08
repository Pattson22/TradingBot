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
SYMBOLS = ["EURUSD", "USDJPY"]

# Working timeframe for signal generation. Uses MetaTrader5 timeframe
# constants (mt5.TIMEFRAME_H1 etc.) — imported where needed to avoid a
# hard dependency on the MT5 package inside this pure-config module.
TIMEFRAME_NAME = "H1"  # resolved to an mt5.TIMEFRAME_* constant in data_feed.py
BARS_TO_FETCH = 300     # enough history for EMA200 + indicator warm-up

# ---------------------------------------------------------------------------
# STRATEGY PARAMETERS (mean reversion, trend-filtered)
# ---------------------------------------------------------------------------
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

BOLLINGER_PERIOD = 20
BOLLINGER_STD_DEV = 2.0

TREND_FILTER_EMA_PERIOD = 200

ATR_PERIOD = 14
ATR_SL_MULTIPLIER = 1.5     # stop-loss distance = ATR * this
ATR_TP_MULTIPLIER = 4.5     # initial safety-net TP distance = ATR * this (~3R)
ATR_TRAIL_MULTIPLIER = 2.0  # trailing stop distance for the runner leg

# ---------------------------------------------------------------------------
# RISK MANAGEMENT
# ---------------------------------------------------------------------------
RISK_PER_TRADE = 0.01  # 1% of current account balance risked per trade

# Multi-tier exit management, expressed in multiples of initial risk (R)
BREAKEVEN_TRIGGER_R = 1.0   # move SL to entry once price reaches +1R
PARTIAL_TP_TRIGGER_R = 2.0  # take partial profit once price reaches +2R
PARTIAL_TP_FRACTION = 0.5   # fraction of position volume closed at that point

# ---------------------------------------------------------------------------
# CIRCUIT BREAKER
# ---------------------------------------------------------------------------
MAX_DAILY_DRAWDOWN_PCT = 0.02  # 2% intraday equity drawdown halts trading

# ---------------------------------------------------------------------------
# SPREAD FILTER
# ---------------------------------------------------------------------------
# Maximum acceptable spread per symbol, expressed in broker "points"
# (MT5 points, not pips — for a 5-digit EURUSD, 1 pip = 10 points).
# Tune these to each symbol's normal spread; widen only for a considered reason.
MAX_SPREAD_POINTS = {
    "EURUSD": 20,   # ~2.0 pip cap
    "USDJPY": 20,
}
DEFAULT_MAX_SPREAD_POINTS = 30  # fallback for symbols not listed above

# ---------------------------------------------------------------------------
# EXECUTION / MISC
# ---------------------------------------------------------------------------
MAGIC_NUMBER = 20260708          # unique id tagging this bot's orders/positions
ORDER_DEVIATION_POINTS = 10      # max allowed slippage on market orders
POLL_INTERVAL_SECONDS = 30       # main loop cadence

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "tradingbot.log")
