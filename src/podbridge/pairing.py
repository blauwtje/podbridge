"""Pairing with a hint before and a next step after a timeout."""

import bumble.core

from podbridge.events import Event

TIMEOUT_MESSAGE = "no AirPods connected in {seconds:g} s; none answered the search under an AirPods name"
TIMEOUT_NEXT_STEP = (
    "take the address from Windows Bluetooth settings and run podbridge run --address <address>"
)


async def pair_with_hint(host, reporter, timeout=60.0, address=None):
    """Return the connection (to `address` when known), or None after reporting why pairing did not happen."""
    reporter.emit(Event("pairing_hint", "Hold the AirPods case button until they pair."))
    try:
        return await host.pair(timeout, address)
    except (TimeoutError, bumble.core.BaseError):
        reporter.emit(Event("pairing_timeout", TIMEOUT_MESSAGE.format(seconds=timeout), TIMEOUT_NEXT_STEP))
        return None
