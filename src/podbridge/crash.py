"""Turn an unhandled error into one message and a run.log entry."""
import traceback

from podbridge.events import Event
from podbridge.paths import appdata_dir

NEXT_STEP = "run podbridge doctor"


def describe(exc) -> Event:
    """One-line cause for any error; DriverError and Bumble errors name their own kind."""
    from bumble.core import BaseBumbleError

    from podbridge.winusb import DriverError

    if isinstance(exc, DriverError):
        prefix = "Driver problem"
    elif isinstance(exc, BaseBumbleError):
        prefix = "Bluetooth problem"
    else:
        prefix = "Unexpected error"
    cause = " ".join(str(exc).split()) or type(exc).__name__
    return Event("failed", f"{prefix}: {cause}", NEXT_STEP)


def restore_failure(exc) -> Event:
    """The driver restore at start failed; say how to finish it."""
    cause = " ".join(str(exc).split()) or type(exc).__name__
    return Event("driver_restore_failed", f"Driver restore failed: {cause}",
                 "run podbridge restore or restart Windows")


def restore_pending(state_path) -> Event | None:
    """A driver state file at start means an earlier run never restored the driver."""
    if not state_path.exists():
        return None
    return Event("driver_restore_pending", "An earlier run left the WinUSB driver in place",
                 "run podbridge restore or restart Windows")


def write_log(exc, path=None) -> None:
    """Append the traceback to run.log; a write error is ignored."""
    try:
        path = path or appdata_dir() / "run.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
