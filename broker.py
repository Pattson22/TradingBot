"""
Broker connection layer.

This is the one module that owns the MetaTrader5 session lifecycle and the
raw account/symbol lookups. Everything else in the bot goes through here
rather than importing `MetaTrader5` directly, so that swapping brokers later
(e.g. to OANDA's REST API) only means rewriting this file plus
`order_execution.py`, not the strategy or risk logic.

Requires:
- A running MetaTrader 5 terminal (Windows binary; via Wine on Linux/Mac, or
  a Windows VM/VPS) logged into a broker account (demo account recommended
  for first runs).
- pip install MetaTrader5
"""

import MetaTrader5 as mt5

import config
from logger_setup import get_logger

log = get_logger(__name__)


class BrokerConnectionError(RuntimeError):
    """Raised when the MT5 terminal cannot be reached or login fails."""


def connect():
    """
    Initialise the MT5 terminal connection and log in to the configured
    account. Must be called once before any other broker.* function.
    """
    init_kwargs = {}
    if config.MT5_PATH:
        init_kwargs["path"] = config.MT5_PATH
    if config.MT5_LOGIN:
        init_kwargs["login"] = config.MT5_LOGIN
    if config.MT5_PASSWORD:
        init_kwargs["password"] = config.MT5_PASSWORD
    if config.MT5_SERVER:
        init_kwargs["server"] = config.MT5_SERVER

    if not mt5.initialize(**init_kwargs):
        error = mt5.last_error()
        raise BrokerConnectionError(f"MT5 initialize() failed: {error}")

    account_info = mt5.account_info()
    if account_info is None:
        raise BrokerConnectionError(f"MT5 login failed: {mt5.last_error()}")

    log.info(
        "Connected to MT5 account %s on server %s | balance=%.2f %s",
        account_info.login,
        account_info.server,
        account_info.balance,
        account_info.currency,
    )

    if not account_info.trade_allowed:
        log.warning(
            "Account reports trade_allowed=False (algo trading may be "
            "disabled in the terminal, or the account is investor/read-only)."
        )

    for symbol in config.SYMBOLS:
        if not mt5.symbol_select(symbol, True):
            log.warning("Could not select symbol %s in Market Watch", symbol)

    return account_info


def disconnect():
    """Cleanly shut down the MT5 connection."""
    mt5.shutdown()
    log.info("Disconnected from MT5")


def get_account_snapshot():
    """
    Return the current balance and equity, fetched fresh from the broker.

    Never cache these values — the risk engine's compounding behaviour
    depends on always sizing off the *current* balance, not a stale one.
    """
    account_info = mt5.account_info()
    if account_info is None:
        raise BrokerConnectionError(f"account_info() failed: {mt5.last_error()}")
    return {
        "balance": account_info.balance,
        "equity": account_info.equity,
        "currency": account_info.currency,
    }


def get_symbol_info(symbol):
    """
    Return the MT5 SymbolInfo named tuple for `symbol`, containing
    tick_size, tick_value, volume_step/min/max, spread, digits, etc.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        raise BrokerConnectionError(f"symbol_info({symbol}) returned None")
    return info


def get_current_tick(symbol):
    """Return the latest bid/ask tick for `symbol`."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise BrokerConnectionError(f"symbol_info_tick({symbol}) returned None")
    return tick


def get_open_positions(symbol=None, magic=config.MAGIC_NUMBER):
    """
    Return this bot's open positions (filtered by magic number so we never
    touch positions opened manually or by another EA), optionally filtered
    to a single symbol.
    """
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if positions is None:
        return []
    return [p for p in positions if p.magic == magic]
