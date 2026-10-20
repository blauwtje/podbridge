"""Start the Bluetooth radio; when it does not answer, run each recovery step and retry."""
import asyncio
import subprocess
from functools import partial

import usb1

from podbridge.events import Event

OPEN_FAILED_STEP = "Run podbridge restore, then try again."
# A warm Windows restart did not revive this radio in the field; a full power-off is the untested next step.
POWER_OFF_STEP = ("Try a full shutdown: run shutdown /s /full /t 0, wait until the laptop is off, "
                  "then switch it on and open podbridge again.")


class RadioError(RuntimeError):
    def __init__(self, message: str, next_step: str):
        super().__init__(message)
        self.next_step = next_step


def _step_errors() -> tuple:
    from podbridge.winusb import DriverError

    return usb1.USBError, DriverError, OSError, subprocess.SubprocessError


async def _start(host, reporter, timeout):
    """Return `host.start()` within `timeout`; turn a USB open error into RadioError."""
    try:
        return await asyncio.wait_for(host.start(), timeout)
    except usb1.USBError as error:
        message = f"Could not open the USB adapter: {error}"
        reporter.emit(Event("usb_open_failed", message, OPEN_FAILED_STEP))
        raise RadioError(message, OPEN_FAILED_STEP) from error


async def start_radio(host, reporter, timeout: float = 30.0, recover=()):
    """Return `host.start()`; on each timeout close the host, run the next recovery step and retry.

    `recover` holds (what, step) pairs: `what` says what the step does, `step` is a blocking callable run
    in a thread. A step that fails is reported and the next one runs. With no step left, raise RadioError.
    """
    steps, tried = iter(recover), []
    while True:
        try:
            return await _start(host, reporter, timeout)
        except asyncio.TimeoutError:
            await host.close()
        for what, step in steps:
            reporter.emit(Event("radio_reset", f"The Bluetooth radio did not answer; {what}.", "Retrying."))
            try:
                await asyncio.to_thread(step)
            except _step_errors() as error:
                cause = " ".join(str(error).split()) or type(error).__name__
                tried.append(f"{what} failed ({cause})")
                reporter.emit(Event("radio_reset", f"{what[:1].upper()}{what[1:]} failed: {cause}.",
                                    "Trying the next step."))
                continue
            tried.append(what)
            break
        else:
            message = "The Bluetooth radio did not answer"
            if tried:
                message += " after " + "; ".join(tried)
            reporter.emit(Event("radio_timeout", message, POWER_OFF_STEP))
            raise RadioError(message, POWER_OFF_STEP) from None


def usb_reset(vid: int, pid: int) -> None:
    """Reset the radio through libusb. On WinUSB libusb can only reset the pipes of a claimed interface."""
    from bumble.transport.usb import load_libusb

    load_libusb()
    with usb1.USBContext() as context:
        handle = context.openByVendorIDAndProductID(vid, pid)
        if handle is None:
            raise OSError(f"no USB device {vid:04X}:{pid:04X} to reset")
        try:
            handle.claimInterface(0)
            handle.resetDevice()
        except usb1.USBErrorNotFound:
            pass  # the device re-enumerated after the reset
        finally:
            handle.close()


def recovery_steps(machine, vid: int, pid: int) -> list:
    """The steps `start_radio` tries in order: a libusb reset, then a Windows device restart."""
    from podbridge import winusb

    return [
        ("resetting its USB connection", partial(usb_reset, vid, pid)),
        ("restarting it in Windows", partial(winusb.restart_device, machine)),
    ]
