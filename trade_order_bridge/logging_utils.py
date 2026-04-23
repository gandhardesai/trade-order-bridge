from __future__ import annotations

import logging

from trade_order_bridge.config import settings


def configure_logging() -> None:
    level_name = settings.log_level.upper().strip()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def request_logger() -> logging.Logger:
    return logging.getLogger("trade_order_bridge.request")
