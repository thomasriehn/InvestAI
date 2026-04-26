"""Logging helpers."""
from __future__ import annotations

import logging
from pathlib import Path

from rich.logging import RichHandler

_INITIALISED = False


def get_logger(name: str = "investai", level: int = logging.INFO,
               log_file: Path | None = None) -> logging.Logger:
    global _INITIALISED
    logger = logging.getLogger(name)
    if not _INITIALISED:
        logger.setLevel(level)
        handler = RichHandler(rich_tracebacks=True, markup=False, show_time=True,
                              show_path=False)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s"))
            logger.addHandler(fh)
        _INITIALISED = True
    return logger
