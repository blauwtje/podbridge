"""AAP command bytes and mic SDU parsing (see .exo/airpods/docs-aap.md)."""

from collections.abc import Iterator

PSM = 0x1001
HANDSHAKE = bytes.fromhex("00000400010002000000000000000000")
FEATURES = bytes.fromhex("040004004D00FF00000000000000")
NOTIFICATIONS = bytes.fromhex("040004000F00FFFFFFFF")
START = bytes.fromhex("04000400580000000900000182000000049600")
STOP = bytes.fromhex("040004005800000002000301")
_CONTROL_PREFIX = bytes.fromhex("040004000900")


def control(id: int, *data: int) -> bytes:
    """The 11-byte control-command frame: prefix, setting id, data bytes d1..d4 (zero padded)."""
    return _CONTROL_PREFIX + bytes([id, *data]).ljust(5, b"\0")


def parse_control(frame: bytes) -> tuple[int, bytes] | None:
    """Return (id, four data bytes) for a control frame, else None."""
    if len(frame) != 11 or frame[:6] != _CONTROL_PREFIX:
        return None
    return frame[6], bytes(frame[7:])


CONVERSATIONAL_AWARENESS_OFF = control(0x28, 2)
CONVERSATIONAL_AWARENESS_ON = control(0x28, 1)

HEADER_LEN = 22
_RECORD_HEAD = 5


def is_mic_sdu(d: bytes) -> bool:
    return len(d) >= 8 and d[0] == 0x04 and d[2] == 0x04 and d[4] == 0x58 and d[6] == 0x01 and d[7] == 0x00


def parse_mic_sdu(d: bytes) -> Iterator[bytes]:
    """Yield each access unit in one mic SDU; stop at a zero length or overrun."""
    if not is_mic_sdu(d):
        return
    pos = HEADER_LEN
    while pos + _RECORD_HEAD <= len(d):
        size = d[pos + 4]
        if size == 0 or pos + _RECORD_HEAD + size > len(d):
            return
        yield bytes(d[pos + _RECORD_HEAD : pos + _RECORD_HEAD + size])
        pos += _RECORD_HEAD + size
