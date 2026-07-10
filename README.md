# Conservative Forex Trading Bot

A rule-based, low-risk-per-trade Forex bot for EUR/USD and AUD/USD, built on
MetaTrader 5. Philosophy: capital preservation first, compounding via small,
consistent gains — no Martingale, no grid trading, no averaging down.

USD/JPY and GBP/USD were both evaluated as candidates and rejected:
`backtest.py`/`optimize.py` showed both structurally losing money with this
strategy across the same 18-month history that EUR/USD and AUD/USD were
profitable on (fixed-default and walk-forward, with and without MTF
confirmation). See git history around the "Drop USDJPY" and "Add AUDUSD,
drop GBPUSD candidate" commits for the full evidence if you're evaluating
another pair.

## Current state (as of 2026-07-10)

- Running live (`DRY_RUN=false`) against an FxPro **demo** account, launched
  via the desktop shortcut described below. No live-cash account is
  connected. Zero trades have fired yet since going live — expected, not a
  bug, given the backtested trade frequency below.
- `config.RISK_PER_TRADE` is deliberately set to **0.25%** (not the 1% a
  fresh checkout might suggest) while confidence builds on live fills;
  step it back up once satisfied. See `config.MAX_PORTFOLIO_RISK_PCT` too.
- The news filter (`news_filter.py`) is wired to jblanked.com's calendar API
  (see Configuration below) but **not yet active** — needs a real API key
  from the user, which hasn't been supplied yet. `NEWS_CALENDAR_URL` unset
  = filter disabled, bot runs exactly as before.
- Two opt-in strategy filters exist but are **disabled by default**
  pending stronger evidence: `SESSION_FILTER_ENABLED` (time-of-day) and
  `VOLATILITY_FILTER_ENABLED` (ATR-percentile regime). A same-day backtest
  comparison found a mild plausible improvement from a broad London+NY
  session filter, but the volatility filter's promising-looking numbers
  were on too few trades (3) to trust. Don't flip either on as a live
  default without more validation first.

## How it works

- **Strategy** (`signals.py`): trend-filtered mean reversion. Trades pullbacks
  to the Bollinger Bands + RSI extremes, but only in the direction of the
  longer-term EMA(200) trend, additionally confirmed against a higher
  timeframe's own trend (`mtf_trend.py`, `config.MTF_TIMEFRAME_NAME` = H4 by
  default) — both timeframes must agree before an entry fires. This is
  deliberately selective: the 18-month backtest produced roughly one trade
  every 5-7 weeks per symbol, so long gaps with no trades are expected.
  Two additional opt-in gates (`SESSION_FILTER_ENABLED`,
  `VOLATILITY_FILTER_ENABLED`, see Current state above) can further
  restrict entries once validated.
- **Risk** (`risk_management.py`): every trade risks a fixed fraction of the
  *current* account balance (`config.RISK_PER_TRADE`), with position size
  derived from the current ATR-based stop distance — never a fixed lot size,
  and never rounded up past the cap. `config.MAX_PORTFOLIO_RISK_PCT` caps
  combined worst-case risk across *all* open positions/symbols at once (not
  just per-trade), so two symbols signaling in the same cycle can't stack
  correlated risk beyond that ceiling. A position's contribution to that
  cap drops to zero once its stop has moved to break-even or better.
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
- **News filter** (`news_filter.py`, `economic_calendar.py`): opt-in — only
  active if `NEWS_CALENDAR_URL` is set (see Configuration below). Blocks new
  entries within a configurable window around High-impact macro events for
  either currency in the pair (time lock) and when the live spread is too
  wide even outside that window (spread lock), since news can leave the book
  gapped for a few extra minutes after the time lock itself clears. Also
  de-risks *existing* positions as a High-impact event approaches — moving
  the stop to break-even or flattening the position, per
  `config.NEWS_PROTECTION_ACTION`. Fails safe: if the calendar source errors,
  it blocks all new entries and recommends closing every open position
  rather than trading blind. `economic_calendar.CachingCalendarProvider`
  reuses fetched calendar data for `NEWS_CALENDAR_CACHE_TTL_SECONDS` (24h by
  default) instead of hitting the API every poll cycle. Run
  `python news_filter.py` for a self-contained timeline demo (no MT5
  connection needed — uses a mock calendar).
- **Auto-reconnect** (`main.py`, `broker.py`): if the MT5 terminal connection
  drops (e.g. the terminal process was closed/crashed), the main loop
  catches `BrokerConnectionError` specifically and retries `broker.reconnect()`
  with linear backoff instead of spinning forever logging the same failure.

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

Every strategy/risk/execution parameter (risk %, portfolio risk cap, ATR
multipliers, RSI thresholds, spread caps, symbols, timeframe, poll interval,
opt-in filter toggles) lives in `config.py` — read through it before running.

**Optional — news filter:** currently wired to **jblanked.com's Calendar
API** specifically (`economic_calendar.transform_jblanked_event`,
`main.py._build_news_guard`) — sign up at jblanked.com, generate an API key
from your profile, then set:

```powershell
$env:NEWS_CALENDAR_URL = "https://www.jblanked.com/news/api/mql5/calendar/week/?impact=High&offset=3"
$env:NEWS_CALENDAR_API_KEY = "your-jblanked-api-key"
```

Left unset by default, the bot runs exactly as before with the filter
disabled. Their free tier is capped at 1 request/day, which is why the URL
above uses the **week** endpoint (not "today") plus a 24h cache — see
`config.py`'s `NEWS_CALENDAR_*` comments for the reasoning, including an
unverified assumption about their `offset` timezone parameter that's worth
spot-checking against a known release time once you have a real key.
To point this at a different provider instead, swap the `transform` and
auth header in `main.py._build_news_guard` — `HttpJsonCalendarProvider` is a
generic JSON adapter, not jblanked-specific.

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
`DRY_RUN` is **not** a persistent environment variable — it must be set
explicitly every time the bot is (re)started, or it silently reverts to true.

## Running

```
python main.py
```

Stop with `Ctrl+C` for a clean shutdown/disconnect. `broker.connect()` will
auto-launch the MT5 terminal itself (via `MT5_PATH`) if it isn't already
running — no separate manual terminal launch needed.

**Desktop shortcut:** `start_bot.bat` (repo root) sets `DRY_RUN=false` and
runs `python main.py` from the correct directory, with the console window
left open so you can watch live logs; closing that window stops the bot. A
Windows desktop shortcut named "Start Trading Bot" points at it — the
simplest way to (re)start the bot without a dev environment open.

**Health monitoring:** `check_status.py` opens its own independent MT5
connection (safe to run alongside the live bot) and reports whether
`main.py` is running, a fresh balance/equity/open-positions snapshot, and a
tail of `tradingbot.log`, appending to `logs/status_check.log`. A Windows
Scheduled Task named `TradingBotHealthCheck` runs it hourly automatically —
recreate it with `schtasks /Create` if setting this up on a new machine (see
git history for the exact command used).

## Backtesting

Before trusting the strategy's parameters (RSI thresholds, ATR multipliers,
etc.), `backtest.py` replays the exact same entry/exit rules against real
historical MT5 bars and reports performance stats — no live orders, no
demo-account side effects:

```
python backtest.py --symbol EURUSD --start 2025-01-01 --end 2026-07-01
```

Requires the same running/logged-in MT5 terminal as live trading (historical
bars and symbol tick economics are both sourced from it). If you're
backtesting a symbol not in `config.SYMBOLS`, select it in the terminal's
Market Watch first (`mt5.symbol_select(symbol, True)`) — `broker.connect()`
only auto-selects the symbols already configured, and MT5's
`copy_rates_range` silently returns nothing for an unselected symbol. Bars
are cached to `backtest_data/` so repeat runs over the same window don't
re-fetch. Output and logs go to `logs/backtest.log`, kept separate from the
live bot's log. See `backtest_engine.py`'s module docstring for the OHLC-bar
approximations this implies (no tick-level intrabar precision, no slippage
modeling) — read it before trusting the exact numbers.

### Walk-forward parameter optimization

`optimize.py` sweeps RSI/Bollinger/ATR threshold parameters (not period
lengths) across sequential in-sample/out-of-sample folds: each fold's
parameters are chosen using only preceding data, then measured on data the
optimizer never saw when choosing them. Only out-of-sample results are
reported — this is what guards against simply overfitting the full
historical dataset and reporting a cherry-picked "best" combo.

```
python optimize.py --symbol EURUSD --start 2025-01-01 --end 2026-07-01
python optimize.py --symbol EURUSD --start 2025-01-01 --end 2026-07-01 \
    --in-sample-bars 3000 --out-sample-bars 750 --objective profit_factor
```

See `backtest_optimize.py`'s module docstring for what's swept and why. A
walk-forward fold's chosen parameter is a claim about that fold only — not
a recommendation to apply it as a permanent fixed default. (RSI 35/65 was
tried as a fixed default after walk-forward runs kept picking it, and made
things measurably worse when applied uniformly instead of adaptively — see
git history / memory around 2026-07-08.)

## Testing

```
pip install -r requirements-dev.txt
pytest
```

Covers the stateless modules (`risk_management.py`, `indicators.py`,
`backtest_engine.py`, `backtest_optimize.py`, `mtf_trend.py`, `signals.py`,
`economic_calendar.py`, `news_filter.py`) and the SQLite persistence layer
(`trade_state_store.py`) directly; live-only modules that require a real MT5
connection (`broker.py`, `order_execution.py`, `trade_manager.py`,
`circuit_breaker.py`, `main.py`) are exercised via the dry-run/live loop
instead, not unit tests.

## Known limitations / follow-ups

- The circuit breaker's "day" boundary uses UTC calendar dates on the
  machine running the bot, not the broker's own server-side trading-day
  rollover — adjust `circuit_breaker.py` if your broker's day boundary
  matters for your use case.
- `backtest_engine.py` only has bar-level (not tick-level) granularity for
  exit-tier management, so its numbers are a reasonable approximation, not a
  perfect replay of what live polling would have done — see its docstring.
- No currently-open position has yet gone through a full live exit-tier
  lifecycle (break-even → partial → trail → close) end-to-end — the demo
  run so far has been quiet. Worth watching closely the first time it does.
- Before adding further symbols, be aware of multiple-comparisons risk: the
  more pairs get backtested, the more likely one looks good by chance alone
  on any fixed historical window. AUDUSD's own supporting sample is still
  small (10-14 trades depending on test).
