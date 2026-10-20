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
