"""Settings catalog, confirmed-write state model and the saved Conversation Awareness choice."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from podbridge import aap, aap_inbound
from podbridge.paths import appdata_dir

CONFIRM_TIMEOUT_S = 3.0
CONFIRMED, PENDING, UNSUPPORTED = "confirmed", "pending", "unsupported"
_ENROLLED_ID = 0x2C
_ON_OFF = (1, 2)
# AAP battery component ids (librepods docs/control_commands.md) and the status that means charging.
_BATTERY_PARTS = {"left": 4, "right": 2, "case": 8}
_CHARGING = 1
_IN_EAR = 0


@dataclass(frozen=True)
class Setting:
    """`kind`: choice (values allowed), range (values = (low, high)), bitmask (values = bits,
    at least two set) or apple_only. `width` > 1: value is a tuple, `values` one tuple per byte."""

    id: int | None
    key: str
    label: str
    kind: str
    values: tuple = ()
    apple_only: bool = False
    width: int = 1


def _on_off(id: int, key: str, label: str) -> Setting:
    return Setting(id, key, label, "choice", _ON_OFF)


def _apple(key: str, label: str) -> Setting:
    return Setting(None, key, label, "apple_only", apple_only=True)


CATALOG = (
    Setting(0x0D, "listening_mode", "Listening mode", "choice", (1, 2, 3, 4)),
    Setting(0x1A, "press_hold_cycle", "Press and hold cycle", "bitmask", (1, 2, 4, 8)),
    _on_off(0x34, "allow_off", "Allow Off"),
    Setting(0x2E, "adaptive_level", "Adaptive level", "choice", (0, 50, 100)),
    _on_off(0x28, "conversation_awareness", "Conversation Awareness"),
    _on_off(0x26, "personalized_volume", "Personalized Volume"),
    Setting(0x16, "press_hold_action", "Press and hold action", "choice", ((1, 5), (1, 5)), width=2),
    Setting(0x17, "press_speed", "Press speed", "choice", (0, 1, 2)),
    Setting(0x18, "press_hold_duration", "Press and hold duration", "choice", (0, 1, 2)),
    _on_off(0x25, "volume_swipe", "Volume swipe"),
    Setting(0x23, "swipe_length", "Swipe length", "choice", (0, 1, 2)),
    Setting(0x24, "mute_end_call", "Mute and end call", "choice", ((0x22, 0x23), (0x02, 0x03)), width=2),
    Setting(0x1F, "tone_volume", "Tone volume", "range", (15, 100)),
    _on_off(0x0A, "ear_detection", "Automatic ear detection"),
    Setting(0x01, "microphone", "Microphone", "choice", (0, 1, 2)),
    _on_off(0x1B, "one_pod_anc", "Noise cancellation with one AirPod"),
    _on_off(0x35, "sleep_pause", "Pause media when falling asleep"),
    _on_off(0x31, "case_sounds", "Charging case sounds"),
    _on_off(0x30, "heart_rate", "Heart rate"),
    _on_off(0x33, "hearing_assist", "Hearing Assist"),
    _apple("firmware_update", "Firmware update"),
    _apple("find_my", "Find My"),
    _apple("hearing_test", "Hearing test"),
    _apple("ear_tip_fit_test", "Ear tip fit test"),
)
_BY_KEY = {s.key: s for s in CATALOG}
_BY_ID = {s.id: s for s in CATALOG if s.id is not None}


def _valid(setting: Setting, value) -> bool:
    if setting.apple_only:
        return False
    if setting.width > 1:
        return (
            isinstance(value, tuple)
            and len(value) == setting.width
            and all(v in allowed for v, allowed in zip(value, setting.values))
        )
    if setting.kind == "range":
        return setting.values[0] <= value <= setting.values[1]
    if setting.kind == "bitmask":
        allowed = sum(setting.values)
        return value & ~allowed == 0 and value.bit_count() >= 2
    return value in setting.values


def _data(setting: Setting, value) -> tuple:
    return value if setting.width > 1 else (value,)


def _decode(setting: Setting, data: bytes):
    return tuple(data[: setting.width]) if setting.width > 1 else data[0]


class SettingsState:
    def __init__(self):
        self.settings: dict[str, dict] = {}
        self.battery: dict[int, aap_inbound.Battery] = {}
        self.ear: aap_inbound.EarState | None = None
        self.device: aap_inbound.DeviceInfo | None = None
        self.ca_level: int | None = None
        self.enrolled: bool | None = None
        self._confirmed: dict[str, object] = {}
        self._deadline: dict[str, float] = {}

    def apply(self, event) -> None:
        """Apply an `aap_inbound` event, or a raw control frame (a setting echo)."""
        if isinstance(event, bytes):
            return self._echo(event)
        if isinstance(event, aap_inbound.Battery):
            self.battery[event.component] = event
        elif isinstance(event, aap_inbound.EarState):
            self.ear = event
        elif isinstance(event, aap_inbound.DeviceInfo):
            self.device = event
        elif isinstance(event, aap_inbound.CaLevel):
            self.ca_level = event.level

    def _echo(self, frame: bytes) -> None:
        parsed = aap.parse_control(frame)
        if parsed is None:
            return
        id, data = parsed
        if id == _ENROLLED_ID:
            self.enrolled = data[0] == 1
        setting = _BY_ID.get(id)
        if setting is None:
            return
        entry = self.settings.get(setting.key)
        value = _decode(setting, data)
        if entry and entry["status"] == PENDING and entry["value"] != value:
            return self._revert(setting.key)
        self._settle(setting.key, value)

    def _settle(self, key: str, value) -> None:
        self._confirmed[key] = value
        self._deadline.pop(key, None)
        self.settings[key] = {"value": value, "status": CONFIRMED}

    def _revert(self, key: str) -> None:
        self._deadline.pop(key, None)
        self.settings[key] = {"value": self._confirmed.get(key), "status": UNSUPPORTED}

    def visible(self, key: str) -> bool:
        """Hearing Assist stays hidden until the AirPods report it enrolled."""
        return key != "hearing_assist" or self.enrolled is True

    def write(self, key: str, value, now: float) -> bytes:
        """Mark `key` pending and return the control frame to send; raise ValueError on a bad value."""
        setting = _BY_KEY.get(key)
        if setting is None or not _valid(setting, value):
            raise ValueError(f"cannot write {value!r} to {key}")
        self._deadline[key] = now + CONFIRM_TIMEOUT_S
        self.settings[key] = {"value": value, "status": PENDING}
        return aap.control(setting.id, *_data(setting, value))

    def view(self) -> dict:
        """The AirPods panel data the window reads (`readAirPods` in ui/src/features/airpods/types.ts).

        Only what the AirPods reported is included, so a fresh state gives {}.
        """
        view: dict = {}
        battery = {
            part: {"level": self.battery[component].level, "charging": self.battery[component].status == _CHARGING}
            for part, component in _BATTERY_PARTS.items()
            if component in self.battery
        }
        if battery:
            view["battery"] = battery
        if self.ear is not None and self.ear.primary == self.ear.secondary:
            # The frame does not say which pod is the left one, so only a state both pods share is shown.
            inside = self.ear.primary == _IN_EAR
            view["in_ear"] = {"left": inside, "right": inside}
        if self.device is not None:
            view.update(name=self.device.name, model=self.device.model, serial=self.device.serial,
                        firmware=self.device.firmware)
        if self.enrolled is not None:
            view["enrolled"] = self.enrolled
        shown = {key: dict(entry) for key, entry in self.settings.items() if self.visible(key)}
        if shown:
            view["settings"] = shown
        return view

    def tick(self, now: float) -> None:
        for key, deadline in list(self._deadline.items()):
            if now >= deadline:
                self._revert(key)


def _settings_path() -> Path:
    return appdata_dir() / "settings.json"


def load_ca_choice(path: Path | None = None) -> int | None:
    """The saved Conversation Awareness choice (1 on, 2 off), or None when missing or corrupt."""
    try:
        choice = json.loads((path or _settings_path()).read_text(encoding="utf-8"))["conversation_awareness"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return choice if type(choice) is int and choice in _ON_OFF else None


def save_ca_choice(choice: int, path: Path | None = None) -> None:
    if type(choice) is not int or choice not in _ON_OFF:
        raise ValueError("conversation awareness choice must be 1 or 2")
    path = path or _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"conversation_awareness": choice}), encoding="utf-8")
    os.replace(tmp, path)
