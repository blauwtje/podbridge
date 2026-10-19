from podbridge import aap


def sdu(*aus, header_tail=b"\x00" * 14):
    head = bytes([4, 0, 4, 0, 0x58, 0, 1, 0]) + header_tail
    body = b"".join(b"\x01\x02\x03\x04" + bytes([len(a)]) + a for a in aus)
    return head + body


def test_command_bytes():
    assert len(aap.HANDSHAKE) == 16
    assert len(aap.FEATURES) == 14
    assert len(aap.NOTIFICATIONS) == 10
    assert len(aap.START) == 19 and aap.START[-3:] == bytes([4, 0x96, 0])
    assert len(aap.STOP) == 12
    assert aap.CONVERSATIONAL_AWARENESS_OFF == bytes.fromhex("0400040009002802000000")
    assert aap.CONVERSATIONAL_AWARENESS_ON == bytes.fromhex("0400040009002801000000")
    assert aap.HANDSHAKE[:6] == bytes.fromhex("000004000100")
    assert aap.FEATURES[4:6] == b"\x4d\x00" and aap.NOTIFICATIONS[4:6] == b"\x0f\x00"


def test_parse_records():
    assert list(aap.parse_mic_sdu(sdu(b"abc", b"de"))) == [b"abc", b"de"]


def test_stop_on_zero_len_and_overrun():
    assert list(aap.parse_mic_sdu(sdu(b"abc", b"") + b"junk")) == [b"abc"]
    assert list(aap.parse_mic_sdu(sdu(b"abc")[:-1])) == []


def test_non_mic_sdu_yields_nothing():
    assert list(aap.parse_mic_sdu(aap.START)) == []
    assert list(aap.parse_mic_sdu(b"")) == []
