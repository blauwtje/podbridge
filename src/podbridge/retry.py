"""Keep reconnecting after any connect failure, with a growing wait."""
import time

import bumble.core

from podbridge.app import RECONNECT_S
from podbridge.events import Event

CONNECT_ERRORS = (bumble.core.BaseBumbleError, OSError, TimeoutError, RuntimeError)
MAX_BACKOFF_S = 30.0
OPEN_CASE_AFTER = 3
LASTED_S = 30.0


def backoff(attempt: int) -> float:
    """Seconds to wait after the given failed attempt (1-based): RECONNECT_S doubling to a 30 s cap."""
    return min(RECONNECT_S * 2 ** max(attempt - 1, 0), MAX_BACKOFF_S)


def report_failure(reporter, exc, attempt: int) -> None:
    reporter.emit(Event("connect_retry", f"Connecting to the AirPods failed: {exc}", "Retrying.", attempt))
    if attempt == OPEN_CASE_AFTER:
        reporter.emit(Event("open_case", "The AirPods are not answering.", "Open the AirPods case lid.", attempt))


async def retry_serve(once, reporter, sleep) -> None:
    """Run `once` forever; a connect error is reported and retried, a connect that lasted resets the count."""
    attempt = 0
    while True:
        started = time.monotonic()
        try:
            await once()
            attempt = 0
        except CONNECT_ERRORS as exc:
            lasted = time.monotonic() - started >= LASTED_S
            attempt = 1 if lasted else attempt + 1
            report_failure(reporter, exc, attempt)
        await sleep(backoff(attempt))
