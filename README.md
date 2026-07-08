# Conservative Forex Trading Bot

A rule-based, low-risk-per-trade Forex bot for EUR/USD and USD/JPY, built on
MetaTrader 5. Philosophy: capital preservation first, compounding via small,
consistent gains — no Martingale, no grid trading, no averaging down.

## How it works

- **Strategy** (`signals.py`): trend-filtered mean reversion. Trades pullbacks
  to the Bollinger Bands + RSI extremes, but only in the direction of the
  longer-term EMA(200) trend.
- **Risk** (`risk_management.py`): every trade risks a fixed 1% of the
  *current* account balance (`config.RISK_PER_TRADE`), with position size
  derived from the current ATR-based stop distance — never a fixed lot size.
- **Execution** (`order_execution.py`): entry, stop-loss, and take-profit are
  sent in one atomic request so a position is never briefly unprotected.
- **Exit management** (`trade_manager.py`): stop moves to break-even at +1R,
  50% of the position is taken off at +2R, and the remainder trails on an
  ATR-based stop. Progress through these tiers is persisted to a small
  SQLite database (`trade_state_store.py`, `data/trade_state.db`) so it
  survives a bot restart.
- **Circuit breaker** (`circuit_breaker.py`): a 2% intraday equity drawdown
  force-closes everything and halts new entries until the next calendar day.
- **Spread filter** (`spread_filter.py`): blocks new entries when the spread
  is wider than the configured baseline (e.g. around news events).

## Requirements

- A **MetaTrader 5 terminal** installed and logged in to a broker account.
  The `MetaTrader5` Python package talks to a running MT5 terminal process
  over a Windows-only IPC mechanism — both the terminal **and** the Python
  interpreter running this bot need to be on Windows (native Windows is by
  far the simplest route; Wine on Linux/Mac is possible but needs a
  Windows build of Python installed inside the Wine prefix, since the pip
  package itself is a compiled Windows binary).
- **Start with a demo/practice account.** Log in to the demo account inside
  the MT5 terminal itself before running the bot.
- Python 3.10+.

## Setup on Windows (recommended path)

1. Copy this entire `tradingbot` folder onto the Windows machine (e.g. via
   USB flash drive, as long as you trust the machine — never copy over
   your `.env`/credentials from an untrusted or shared drive).
2. Install the MetaTrader 5 terminal on that machine (download from your
   broker's site/email or the official metatrader5.com installer — the
   installer works with any broker) and log in with your demo account's
   login/password/server.
3. Install Python from python.org if it isn't already there (check **"Add
   Python to PATH"** during install), then from a terminal (cmd or
   PowerShell) inside the copied folder:

   ```
   pip install -r requirements.txt
   ```

## Configuration

All credentials are read from environment variables — never hardcode them.

**PowerShell:**
```powershell
$env:MT5_LOGIN = "12345678"
$env:MT5_PASSWORD = "your-demo-account-password"
$env:MT5_SERVER = "FxPro-Demo"          # exact server name from your demo email
$env:MT5_PATH = "C:\Program Files\MetaTrader 5\terminal64.exe"  # optional
```

**macOS/Linux (bash):**
```bash
export MT5_LOGIN=12345678
export MT5_PASSWORD="your-demo-account-password"
export MT5_SERVER="FxPro-Demo"
export MT5_PATH="C:\Program Files\MetaTrader 5\terminal64.exe"  # optional
```

Every strategy/risk/execution parameter (risk %, ATR multipliers, RSI
thresholds, spread caps, symbols, timeframe, poll interval) lives in
`config.py` — read through it before running.

### DRY_RUN (start here)

```powershell
$env:DRY_RUN = "true"   # default; logs signals, sizing, and order requests
                         # WITHOUT sending anything to the broker
```

Run with `DRY_RUN=true` first and watch the logs (`logs/tradingbot.log`) for
at least a few full trading days to confirm signals and position sizing look
correct before setting `DRY_RUN=false` against a demo account. Note that
`DRY_RUN` only skips the final order-send calls — the bot still needs a
real, logged-in MT5 terminal connection to fetch balance, prices, and OHLC
history, so the terminal must be running and logged in even during a dry run.

## Running

```
python main.py
```

Stop with `Ctrl+C` for a clean shutdown/disconnect.

## Backtesting

Before trusting the strategy's parameters (RSI thresholds, ATR multipliers,
etc.), `backtest.py` replays the exact same entry/exit rules against real
historical MT5 bars and reports performance stats — no live orders, no
demo-account side effects:

```
python backtest.py --symbol EURUSD --start 2025-01-01 --end 2026-07-01
```

Requires the same running/logged-in MT5 terminal as live trading (historical
bars and symbol tick economics are both sourced from it). Bars are cached to
`backtest_data/` so repeat runs over the same window don't re-fetch. Output
and logs go to `logs/backtest.log`, kept separate from the live bot's log.
See `backtest_engine.py`'s module docstring for the OHLC-bar approximations
this implies (no tick-level intrabar precision, no slippage modeling) —
read it before trusting the exact numbers.

## Testing

```
pip install -r requirements-dev.txt
pytest
```

Covers the stateless modules (`risk_management.py`, `indicators.py`,
`backtest_engine.py`) and the SQLite persistence layer
(`trade_state_store.py`) directly; live-only modules that require a real MT5
connection (`broker.py`, `order_execution.py`, `trade_manager.py`,
`circuit_breaker.py`) are exercised via the dry-run/live loop instead, not
unit tests.

## Known limitations / follow-ups

- The circuit breaker's "day" boundary uses UTC calendar dates on the
  machine running the bot, not the broker's own server-side trading-day
  rollover — adjust `circuit_breaker.py` if your broker's day boundary
  matters for your use case.
- `backtest_engine.py` only has bar-level (not tick-level) granularity for
  exit-tier management, so its numbers are a reasonable approximation, not a
  perfect replay of what live polling would have done — see its docstring.
