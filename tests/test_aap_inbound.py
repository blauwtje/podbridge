"""Inbound AAP parsers. Fixture layouts: librepods docs (opcodes, device-info), MagicPodsCore src/sdk/aap/."""

from podbridge.aap_inbound import Battery, CaLevel, DeviceInfo, EarState, parse

P = "04000400"


def test_battery_components_and_status():
    frame = bytes.fromhex(P + "0400" + "03" + "04014B0101" + "0201320201" + "08015A0201")
    assert parse(frame) == [Battery(4, 0x4B, 1), Battery(2, 0x32, 2), Battery(8, 0x5A, 2)]


def test_battery_drops_levels_above_100():
    frame = bytes.fromhex(P + "0400" + "02" + "08017F0201" + "0401640101")
    assert parse(frame) == [Battery(4, 100, 1)]


def test_battery_truncated_is_none():
    assert parse(bytes.fromhex(P + "0400" + "02" + "0401640101")) is None


def test_ear_state():
    assert parse(bytes.fromhex(P + "0600" + "0001")) == [EarState(0, 1)]
    assert parse(bytes.fromhex(P + "0600" + "00")) is None


def test_device_info_strings():
    strings = b"My Pods\0AirPods Pro\0Apple\0SN123\0 8A357\0"
    frame = bytes.fromhex(P + "1D00" + "01") + strings
    assert parse(frame) == [DeviceInfo("My Pods", "AirPods Pro", "SN123", " 8A357")]


def test_ca_level():
    assert parse(bytes.fromhex(P + "4B00" + "020001" + "03")) == [CaLevel(3)]
    assert parse(bytes.fromhex(P + "4B00" + "0200")) is None


def test_unknown_or_short_is_none():
    assert parse(bytes.fromhex(P + "0900" + "0D02000000")) is None
    assert parse(b"\x04\x00") is None
    assert parse(b"\x00\x00\x04\x00\x04\x00\x00") is None
