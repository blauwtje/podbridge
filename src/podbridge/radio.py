"""Start the Bluetooth radio, resetting it once if it does not answer."""
import asyncio

import usb1

from podbridge.events import Event


class RadioError(RuntimeError):
    pass


async def _start(host, reporter, timeout):
    """Return `host.start()` within `timeout`; turn a USB open error into RadioError."""
    try:
        return await asyncio.wait_for(host.start(), timeout)
    except usb1.USBError as error:
        message = f"Could not open the USB adapter: {error}"
        reporter.emit(Event("usb_open_failed", message, "Run podbridge restore, then try again."))
        raise RadioError(message) from error


async def start_radio(host, reporter, **options):
    """Return `host.start()`; on a timeout close, reset and retry once, then raise RadioError.

    Options: `timeout` seconds (default 30.0) and `reset`, a callable such as usb1 `resetDevice`.
    """
    timeout, reset = options.get("timeout", 30.0), options.get("reset")
    try:
        return await _start(host, reporter, timeout)
    except asyncio.TimeoutError:
        await host.close()
        if reset is not None:
            reset()
        reporter.emit(Event("radio_reset", "The Bluetooth radio did not answer; resetting it.", "Retrying."))
    try:
        return await _start(host, reporter, timeout)
    except asyncio.TimeoutError:
        await host.close()
        message = "Bluetooth radio did not answer; unplug power or reboot"
        reporter.emit(Event("radio_timeout", message, "Unplug the adapter or reboot."))
        raise RadioError(message) from None
