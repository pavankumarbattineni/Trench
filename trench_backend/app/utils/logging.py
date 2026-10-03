import logging
import sys

# Third-party clients (HTTP transports, the vector store SDK, asyncio's own
# internals) log noisy request/connection-level detail at INFO/DEBUG that
# swamps the app's own meaningful logs. They're not useful day-to-day, only
# when actively debugging one of those libraries -- so they're held to
# WARNING regardless of the app's own log level.
_NOISY_LOGGERS = (
    "httpcore",
    "httpx",
    "urllib3",
    "asyncio",
    "pinecone",
    "pinecone_plugin_interface",
)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stdout,
        force=True,
    )
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
