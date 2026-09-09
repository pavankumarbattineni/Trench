import logging
import sys

from app.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = "DEBUG" if settings.is_dev else "INFO"
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stdout,
        force=True,
    )
