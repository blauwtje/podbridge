"""Show when Bumble skips the Realtek firmware patch; the warning still reaches stderr."""
import logging

from podbridge.events import Event

LOGGER = "bumble.drivers.rtk"
MARKER = "no valid patch found"


class _Handler(logging.Handler):
    def __init__(self, reporter):
        super().__init__(logging.WARNING)
        self.reporter = reporter

    def emit(self, record):
        if MARKER in record.getMessage():
            self.reporter.emit(Event(
                "firmware_unverified",
                "The firmware patch for this chip's ROM version was skipped; the Bluetooth radio may run unpatched.",
                "Report the log file.",
            ))


def install(reporter) -> logging.Handler:
    handler = _Handler(reporter)
    logging.getLogger(LOGGER).addHandler(handler)
    return handler


def remove(handler) -> None:
    logging.getLogger(LOGGER).removeHandler(handler)
