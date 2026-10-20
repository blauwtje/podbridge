"""Reporter events: what a run step tells the user, and a console reporter."""

import sys
from dataclasses import dataclass

KINDS = (
    "connect_retry",
    "open_case",
    "pairing_hint",
    "pairing_timeout",
    "bond_dropped",
    "audio_lost",
    "audio_restarting",
    "audio_failed",
    "aap_retry",
    "mic_unavailable",
    "mic_silent",
    "radio_timeout",
    "radio_reset",
    "usb_open_failed",
    "firmware_unverified",
    "driver_restore_pending",
    "driver_restore_failed",
    "failed",
)


@dataclass(frozen=True)
class Event:
    kind: str
    message: str
    next_step: str = ""
    attempt: int = 0


class ConsoleReporter:
    def emit(self, event):
        """Print the message and next step to stderr; unknown kinds still print."""
        print(event.message, file=sys.stderr)
        if event.next_step:
            print(event.next_step, file=sys.stderr)
