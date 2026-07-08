"""
Centralised logging configuration.

Every module calls `logging.getLogger(__name__)` and inherits this setup via
`get_logger()` / `configure_logging()` being called once from main.py. Logs go
to both console and a rotating file so trade history survives restarts.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

import config


def configure_logging(level=logging.INFO, log_file=None):
    """Set up root logging handlers. Call once, at process start.

    `log_file` defaults to config.LOG_FILE (the live bot's log); pass a
    different path (e.g. from backtest.py) to keep one-off/offline runs out
    of the live log that's being actively monitored.
    """
    log_file = log_file or config.LOG_FILE
    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=5
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)


def get_logger(name):
    return logging.getLogger(name)
