"""config/logging_setup.py — Rotating log file + console handler."""

import logging
import logging.handlers
from pathlib import Path


def setup_logging(log_dir: str = "logs", level: str = "INFO"):
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Rotating file handler
    fh = logging.handlers.RotatingFileHandler(
        filename=f"{log_dir}/bot.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=7
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Separate ERROR-only log
    eh = logging.handlers.RotatingFileHandler(
        filename=f"{log_dir}/errors.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=7
    )
    eh.setLevel(logging.ERROR)
    eh.setFormatter(fmt)
    root.addHandler(eh)
