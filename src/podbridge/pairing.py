"""Pairing with a hint before and a next step after a timeout."""

import bumble.core

from podbridge.events import Event

TIMEOUT_MESSAGE = "no AirPods connected in 60 s; they may not connect on their own in pairing mode"
TIMEOUT_NEXT_STEP = (
    "take the address from Windows Bluetooth settings and run podbridge run --address <address>"
)


async def pair_with_hint(host, reporter, timeout=60.0):
    """Return the connection, or None after reporting why pairing did not happen."""
    reporter.emit(Event("pairing_hint", "Hold the AirPods case button until they pair."))
    try:
        return await host.pair(timeout)
    except (TimeoutError, bumble.core.BaseError):
        reporter.emit(Event("pairing_timeout", TIMEOUT_MESSAGE, TIMEOUT_NEXT_STEP))
        return None
