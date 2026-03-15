"""
core/logging.py
Configures structured application-wide logging.
"""

import logging
import sys
from pathlib import Path


def setup_logging(log_file: Path | None = None, debug: bool = False) -> None:
    """
    Set up root logger with a console handler and an optional file handler.
    Call once at application startup.
    """
    level = logging.DEBUG if debug else logging.INFO

    fmt = "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s"
    datefmt = "%H:%M:%S"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt=datefmt,
        handlers=handlers,
        force=True,
    )

    # Quiet noisy third-party loggers
    for noisy in ("urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
