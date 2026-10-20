"""Inbound AAP frames: battery, ear state, device info, CA level (.exo/airpods-ui/research-settings.md)."""

from dataclasses import dataclass

_PREFIX = bytes.fromhex("04000400")
_BATTERY, _EAR, _DEVICE_INFO, _CA_LEVEL = 0x0004, 0x0006, 0x001D, 0x004B
_MAX_LEVEL = 100


@dataclass(frozen=True)
class Battery:
    component: int
    level: int
    status: int


@dataclass(frozen=True)
class EarState:
    primary: int
    secondary: int


@dataclass(frozen=True)
class DeviceInfo:
    name: str
    model: str
    serial: str
    firmware: str


@dataclass(frozen=True)
class CaLevel:
    level: int


def _battery(frame: bytes) -> list | None:
    if len(frame) < 7:
        return None
    entries = [frame[7 + 5 * i : 12 + 5 * i] for i in range(frame[6])]
    if any(len(entry) < 5 for entry in entries):
        return None
    return [Battery(e[0], e[2], e[3]) for e in entries if e[2] <= _MAX_LEVEL]


def _ear(frame: bytes) -> list | None:
    return [EarState(frame[6], frame[7])] if len(frame) >= 8 else None

def _device_info(frame: bytes) -> list | None:

    fields = [part.decode("utf-8", "replace") for part in frame[7:].split(b"\0")]
    if len(fields) < 5:
        return None
    return [DeviceInfo(name=fields[0], model=fields[1], serial=fields[3], firmware=fields[4])]


def _ca_level(frame: bytes) -> list | None:
    return [CaLevel(frame[9])] if len(frame) >= 10 else None


_PARSERS = {_BATTERY: _battery, _EAR: _ear, _DEVICE_INFO: _device_info, _CA_LEVEL: _ca_level}


def parse(frame: bytes) -> list | None:
    """Return the events in `frame`, or None when the opcode is unknown or the frame is short."""
    if len(frame) < 6 or frame[:4] != _PREFIX:
        return None
    parser = _PARSERS.get(int.from_bytes(frame[4:6], "little"))
    return parser(frame) if parser else None
