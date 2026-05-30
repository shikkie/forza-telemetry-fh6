"""
Optional centralized logging configuration.

The CLI already sets up Rich logging. This module exists for advanced users
who want to integrate the collector into larger applications.
"""

from __future__ import annotations

import logging

from rich.logging import RichHandler


def configure_logging(level: str = "INFO", show_path: bool = False) -> None:
    """Configure root logger with Rich handler."""
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=show_path)],
        force=True,
    )

    # Quieter third-party loggers
    logging.getLogger("motor").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
