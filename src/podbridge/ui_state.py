"""Session state the window shows: run steps, notices, one error, AirPods state."""

import json
import threading

STEPS = ("checks", "driver", "firmware", "radio", "pairing", "connecting", "connected")
NOTICE_KINDS = (
    "connect_retry",
    "open_case",
    "pairing_hint",
    "bond_dropped",
    "audio_lost",
    "audio_restarting",
    "aap_retry",
    "mic_unavailable",
    "mic_silent",
    "radio_reset",
    "firmware_unverified",
    "driver_restore_pending",
)
ERROR_KINDS = (
    "pairing_timeout",
    "radio_timeout",
    "usb_open_failed",
    "audio_failed",
    "driver_restore_failed",
    "failed",
)
STATUSES = ("pending", "active", "done", "failed")


class UiState:
    def __init__(self):
        self._lock = threading.Lock()
        self._steps = {name: "pending" for name in STEPS}
        self._attempt = 0
        self._notices = []
        self._error = None
        self._airpods = {}
        self._subscribers = []
        self._writer = None
        self._cancel_session = None
        self._stopping = False

    def subscribe(self, fn):
        """Call fn(snapshot) after every change."""
        self._subscribers.append(fn)

    def set_step(self, name, status, attempt=None):
        """Set one step's status; connecting may carry its attempt count."""
        if name not in self._steps or status not in STATUSES:
            raise ValueError(f"unknown step or status: {name} {status}")
        with self._lock:
            self._steps[name] = status
            if attempt is not None:
                self._attempt = attempt
        self._publish()

    def set_airpods(self, data):
        with self._lock:
            self._airpods = dict(data)
        self._publish()

    def set_writer(self, fn):
        """Install the callable `write_setting` uses (None removes it)."""
        self._writer = fn

    def write_setting(self, key, value):
        """Send a setting change to the AirPods; raises when no session can take it."""
        writer = self._writer
        if writer is None:
            raise RuntimeError("the AirPods are not connected yet")
        writer(key, value)

    def attach_session(self, cancel):
        """Register how to end the running session; a stop that came first ends it at once."""
        self._cancel_session = cancel
        if self._stopping:
            cancel()

    def stop(self):
        """End the running session (called from the window thread when the window closes)."""
        self._stopping = True
        cancel = self._cancel_session
        if cancel is not None:
            cancel()

    def apply(self, event):
        """Record an event: fatal kinds set the error, all others become notices."""
        with self._lock:
            if event.kind in ERROR_KINDS:
                self._set_error(event)
            else:
                self._add_notice(event)
        self._publish()

    def _set_error(self, event):
        self._error = {
            "message": event.message,
            "next_step": event.next_step,
            "details": f"{event.kind}: {event.message}",
        }
        active = [name for name, status in self._steps.items() if status == "active"]
        for name in active:
            self._steps[name] = "failed"

    def _add_notice(self, event):
        if event.kind == "connect_retry":
            self._attempt = event.attempt
        self._notices.append(
            {
                "kind": event.kind,
                "message": event.message,
                "next_step": event.next_step,
                "attempt": event.attempt,
            }
        )

    emit = apply

    def snapshot(self):
        with self._lock:
            data = {
                "steps": [self._step(name) for name in STEPS],
                "notices": list(self._notices),
                "error": dict(self._error) if self._error else None,
                "airpods": dict(self._airpods),
            }
        return json.loads(json.dumps(data, default=str))

    def _step(self, name):
        step = {"name": name, "status": self._steps[name]}
        if name == "connecting":
            step["attempt"] = self._attempt
        return step

    def _publish(self):
        snap = self.snapshot()
        for fn in list(self._subscribers):
            fn(snap)


class Api:
    """The object the window exposes to JavaScript."""

    def __init__(self, state, set_setting_fn):
        self._state = state
        self._set_setting = set_setting_fn

    def get_snapshot(self):
        return self._state.snapshot()

    def set_setting(self, key, value):
        try:
            self._set_setting(key, value)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "error": None}

    def copy_details(self):
        snap = self._state.snapshot()
        lines = []
        if snap["error"]:
            err = snap["error"]
            lines += [err["message"], err["next_step"], err["details"]]
        lines += [n["message"] for n in snap["notices"]]
        return "\n".join(line for line in lines if line)
