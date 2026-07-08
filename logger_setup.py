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


def configure_logging(level=logging.INFO):
    """Set up root logging handlers. Call once, at process start."""
    os.makedirs(config.LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        config.LOG_FILE, maxBytes=5_000_000, backupCount=5
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)


def get_logger(name):
    return logging.getLogger(name)
