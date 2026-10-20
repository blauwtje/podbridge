"""Restart an audio pipeline whose device was lost, giving up after a few tries."""
import logging
from typing import Callable, NamedTuple

from podbridge.events import Event

log = logging.getLogger("podbridge")
LOST_ERRORS = (OSError, RuntimeError)


class Pipeline(NamedTuple):
    name: str
    factory: Callable
    reopen: Callable


class AudioGaveUp(RuntimeError):
    pass


async def supervise(pipeline, reporter, attempts=3) -> None:
    """Await factory(); on a lost device re-pin it with reopen() and restart, up to `attempts` times."""
    name, factory, reopen = pipeline
    attempt = 0
    while True:
        try:
            log.info("pipeline %s starts (restart %d)", name, attempt)
            await factory()
            return
        except LOST_ERRORS as exc:
            cause = exc
            log.error("pipeline %s crashed: %r", name, exc, exc_info=True)
        reporter.emit(Event("audio_lost", f"The {name} audio device was lost: {cause}"))
        attempt += 1
        if attempt > attempts:
            break
        reporter.emit(Event("audio_restarting", f"Restarting {name} audio.", "", attempt))
        try:
            reopen()
        except LOST_ERRORS as exc:
            cause = exc
            reporter.emit(Event("audio_lost", f"Reopening the {name} audio device failed: {cause}"))
    log.error("pipeline %s gives up after %d restarts: %r", name, attempts, cause)
    reporter.emit(Event("audio_failed", f"The {name} audio could not be restored: {cause}", "Reconnect the audio device and run again."))
    raise AudioGaveUp(f"{name} audio gave up: {cause}") from cause
