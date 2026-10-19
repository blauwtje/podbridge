"""AAP command bytes and mic SDU parsing (see .exo/airpods/docs-aap.md)."""

from collections.abc import Iterator

PSM = 0x1001
HANDSHAKE = bytes.fromhex("00000400010002000000000000000000")
FEATURES = bytes.fromhex("040004004D00FF00000000000000")
NOTIFICATIONS = bytes.fromhex("040004000F00FFFFFFFF")
START = bytes.fromhex("04000400580000000900000182000000049600")
STOP = bytes.fromhex("040004005800000002000301")
CONVERSATIONAL_AWARENESS_OFF = bytes.fromhex("0400040009002802000000")
CONVERSATIONAL_AWARENESS_ON = CONVERSATIONAL_AWARENESS_OFF[:7] + b"\x01" + CONVERSATIONAL_AWARENESS_OFF[8:]

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
