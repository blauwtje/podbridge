import asyncio
import math
import os
import struct

import pytest
from pyee import EventEmitter
from bumble import a2dp, avdtp, core, sdp, utils
from bumble.keys import PairingKeys

from podbridge import host, paths
from podbridge.a2dp_encoder import A2dpEncoder

HFP_HSP = {0x1108, 0x1112, 0x111E, 0x111F}


class FakeLocal(EventEmitter):
    EVENT_START, EVENT_SUSPEND = "start", "suspend"

    def __init__(self, codec_type, pump):
        super().__init__()
        self.codec_type, self.pump = codec_type, pump
        self.packet_pump = pump

    async def start(self):
        return None

    async def on_start_command(self):
        return None

    async def on_suspend_command(self):
        return None


class Null:
    def set_packet_sink(self, sink): ...
    def on_packet(self, packet): ...


def tone(seconds=0.5):
    return b"".join(struct.pack("<hh", v, v) for v in (int(12000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(int(seconds * 48000))))


def uuids(record):
    found = set()

    def walk(el):
        if el.type == sdp.DataElement.UUID:
            found.add(el.value)
        elif el.type == sdp.DataElement.SEQUENCE:
            for child in el.value:
                walk(child)

    for attribute in record:
        walk(attribute.value)
    return found


def test_sdp_has_a2dp_source_and_apple_device_id_and_no_hands_free():
    records = host.sdp_records()
    all_uuids = set().union(*(uuids(r) for r in records.values()))
    assert core.BT_AUDIO_SOURCE_SERVICE in all_uuids
    assert core.BT_PNP_INFORMATION_SERVICE in all_uuids
    assert not {u for u in all_uuids if u.to_bytes()[:2] == b"\x00\x00" and int.from_bytes(u.to_bytes()[2:4], "big") in HFP_HSP}
    vendor = [a for a in records[host.DEVICE_ID_HANDLE] if a.id == host.DID_VENDOR_ID][0]
    assert vendor.value.value == 0x004C


def test_device_uses_json_keystore_and_registers_records(tmp_path):
    device = host.build_device(Null(), Null(), keystore_file=tmp_path / "sub" / "keys.json")
    assert device.classic_enabled
    assert set(device.sdp_service_records) == {host.A2DP_SOURCE_HANDLE, host.DEVICE_ID_HANDLE}

    async def roundtrip():
        await device.keystore.update("AA:BB:CC:DD:EE:FF", PairingKeys())
        return await device.keystore.get("AA:BB:CC:DD:EE:FF")

    assert asyncio.run(roundtrip()) is not None
    assert (tmp_path / "sub" / "keys.json").exists()


def test_firmware_env_follows_firmware_dir(monkeypatch, tmp_path):
    import hashlib

    from podbridge import app

    firmware = b"Realtech" + b"x" * 64
    monkeypatch.setattr(app, "FIRMWARE_SHA256", hashlib.sha256(firmware).hexdigest())
    monkeypatch.setenv("APPDATA", str(tmp_path))
    fw = tmp_path / "podbridge" / "firmware"
    fw.mkdir(parents=True)
    (fw / "rtl8852bu_fw.bin").write_bytes(firmware)
    monkeypatch.delenv("BUMBLE_RTK_FIRMWARE_DIR", raising=False)
    assert host.use_firmware_folder() == str(fw)
    assert os.environ["BUMBLE_RTK_FIRMWARE_DIR"] == str(fw)
    assert paths.firmware_dir() == fw


@pytest.mark.parametrize("codec", ["aac", "sbc"])
def test_encoder_output_becomes_rtp_packets(codec):
    async def run():
        source = host.EncodedPacketSource(A2dpEncoder(codec))
        source.feed(tone())
        gen = aiter(source.packets)
        return [await asyncio.wait_for(gen.__anext__(), 1) for _ in range(3)]

    packets = asyncio.run(run())
    assert [p.sequence_number for p in packets] == [0, 1, 2]
    assert packets[1].timestamp_seconds > packets[0].timestamp_seconds
    parsed = avdtp.MediaPacket.from_bytes(bytes(packets[0]))
    assert parsed.payload_type == 96
    if codec == "sbc":
        assert parsed.payload[0] == 1 and parsed.payload[1] == 0x9C


def test_queue_drops_oldest_when_full():
    source = host.EncodedPacketSource(A2dpEncoder("aac"))
    source.feed(tone(5))
    assert source._queue.qsize() == host.PACKET_QUEUE


@pytest.mark.parametrize("codec", ["aac", "sbc"])
def test_codec_capabilities_serialise(codec):
    caps = host.codec_capabilities(codec)
    assert caps.service_capabilities_bytes


class FakeProtocol:
    """One AVDTP signaling channel with an AAC and an SBC sink; configuring a codec in `rejects` fails."""

    def __init__(self, rejects):
        self.rejects, self.discovers, self.started = rejects, 0, []

    async def discover_remote_endpoints(self):
        self.discovers += 1

    def find_remote_sink_by_codec(self, media_type, codec_type):
        return codec_type

    def add_source(self, capabilities, pump):
        return FakeLocal(capabilities.media_codec_type, pump)

    async def create_stream(self, local, sink):
        codec_type, pump = local.codec_type, local.pump
        assert codec_type == sink
        if codec_type in self.rejects:
            raise avdtp.ProtocolError(0x29, "avdtp")  # UNSUPPORTED_CONFIGURATION
        started = self.started

        class Stream:
            async def start(self):
                started.append(codec_type)

        return Stream()


def _avdtp_peer(monkeypatch, rejects):
    protocol, connects = FakeProtocol(rejects), []

    async def find(connection):
        return (1, 3)

    async def connect(connection, version):
        connects.append(connection)
        return protocol

    monkeypatch.setattr(avdtp, "find_avdtp_service_with_connection", find)
    monkeypatch.setattr(avdtp.Protocol, "connect", connect)
    return protocol, connects


def test_stream_falls_back_to_sbc_on_the_same_avdtp_connection(monkeypatch):
    protocol, connects = _avdtp_peer(monkeypatch, {a2dp.A2DP_MPEG_2_4_AAC_CODEC_TYPE})
    h = host.Host("usb:0", codec="aac")
    asyncio.run(h.stream("acl"))
    assert connects == ["acl"] and protocol.discovers == 1
    assert protocol.started == [a2dp.A2DP_SBC_CODEC_TYPE]
    assert h.codec == "sbc" and h.source.encoder.codec == "sbc"


def test_stream_keeps_aac_when_the_peer_accepts_it(monkeypatch):
    protocol, connects = _avdtp_peer(monkeypatch, set())
    h = host.Host("usb:0", codec="aac")
    asyncio.run(h.stream("acl"))
    assert len(connects) == 1 and protocol.started == [a2dp.A2DP_MPEG_2_4_AAC_CODEC_TYPE]
    assert h.codec == "aac"


def test_stream_raises_the_sbc_error_with_the_aac_error_as_its_context(monkeypatch):
    _avdtp_peer(monkeypatch, {a2dp.A2DP_MPEG_2_4_AAC_CODEC_TYPE, a2dp.A2DP_SBC_CODEC_TYPE})
    h = host.Host("usb:0", codec="aac")
    with pytest.raises(avdtp.ProtocolError) as info:
        asyncio.run(h.stream("acl"))
    assert isinstance(info.value.__context__, avdtp.ProtocolError)


AIRPODS = "AA:BB:CC:DD:EE:FF"
PHONE = "11:22:33:44:55:66"


class FakeLink:
    def __init__(self, address, calls):
        self.peer_address, self.calls = address, calls

    async def authenticate(self):
        self.calls.append(("authenticate", self.peer_address))

    async def encrypt(self):
        self.calls.append(("encrypt", self.peer_address))

    async def disconnect(self, *args, **kwargs):
        self.calls.append(("disconnect", self))


class FakeRadio(utils.EventEmitter):
    """A Bumble Device stand-in: inquiry answers with `found`, `accept` waits for `incoming`."""

    def __init__(self, found=(), incoming=None):
        super().__init__()
        self.found, self.incoming, self.calls, self.discovering = list(found), incoming, [], False

    async def set_discoverable(self, on):
        self.calls.append(("discoverable", on))

    async def set_connectable(self, on):
        self.calls.append(("connectable", on))

    async def start_discovery(self):
        self.calls.append(("start_discovery",))
        self.discovering = True
        loop = asyncio.get_running_loop()
        for address, name in self.found:
            data = core.AdvertisingData([(core.AdvertisingData.COMPLETE_LOCAL_NAME, name.encode())])
            loop.call_soon(self.emit, "inquiry_result", address, 0x240404, data, -50)

    async def stop_discovery(self):
        self.calls.append(("stop_discovery",))
        self.discovering = False

    async def connect(self, address, transport=None):
        self.calls.append(("connect", str(address), transport))
        return FakeLink(str(address), self.calls)

    async def accept(self, *args, **kwargs):
        if self.incoming is None:
            await asyncio.Event().wait()
        await asyncio.sleep(0)
        return FakeLink(self.incoming, self.calls)


def paired(radio, timeout=1.0, address=None):
    h = host.Host("usb:0")
    h.device = radio
    return asyncio.run(h.pair(timeout, address))


def test_pair_finds_the_airpods_by_inquiry_and_connects_to_them():
    radio = FakeRadio(found=[(PHONE, "Bob's phone"), (AIRPODS, "Thomas's AirPods Pro")])
    link = paired(radio)
    assert link.peer_address == AIRPODS
    assert ("connect", AIRPODS, core.PhysicalTransport.BR_EDR) in radio.calls
    assert not any(c[0] == "connect" and c[1] == PHONE for c in radio.calls)
    assert radio.calls.index(("stop_discovery",)) < radio.calls.index(("connect", AIRPODS, core.PhysicalTransport.BR_EDR))
    assert radio.calls[-2:] == [("authenticate", AIRPODS), ("encrypt", AIRPODS)]
    assert radio.listeners("inquiry_result") == []


def test_pair_takes_the_airpods_that_connect_on_their_own():
    radio = FakeRadio(incoming=AIRPODS)
    link = paired(radio)
    assert link.peer_address == AIRPODS
    assert not radio.discovering
    assert radio.calls[-2:] == [("authenticate", AIRPODS), ("encrypt", AIRPODS)]


def test_pair_with_a_known_address_connects_to_it_without_inquiry():
    radio = FakeRadio()
    link = paired(radio, address=AIRPODS)
    assert link.peer_address == AIRPODS
    assert ("start_discovery",) not in radio.calls
    assert ("connect", AIRPODS, core.PhysicalTransport.BR_EDR) in radio.calls


def test_pair_times_out_once_and_stops_the_inquiry():
    radio = FakeRadio(found=[(PHONE, "Bob's phone")])
    with pytest.raises(TimeoutError):
        paired(radio, timeout=0.2)
    assert ("start_discovery",) in radio.calls
    assert not radio.discovering
    assert radio.listeners("inquiry_result") == []


class SlowPageRadio(FakeRadio):
    """The page to a known address never answers; cancelling it fails in the controller."""

    async def connect(self, address, transport=None):
        self.calls.append(("connect", str(address), transport))
        await asyncio.Event().wait()

    async def cancel_connection(self, address):
        self.calls.append(("cancel_connection", str(address)))
        raise core.InvalidStateError("no page to cancel")


def test_a_failing_page_cancel_is_logged_and_the_incoming_connection_still_wins(caplog):
    radio = SlowPageRadio(incoming=AIRPODS)
    with caplog.at_level("WARNING", logger="podbridge"):
        link = paired(radio, address=AIRPODS)
    assert link.peer_address == AIRPODS
    assert ("cancel_connection", AIRPODS) in radio.calls
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and isinstance(warnings[0].exc_info[1], core.InvalidStateError)


class BothConnectRadio(FakeRadio):
    """The AirPods connect in and answer the page in the same loop turn."""

    async def connect(self, address, transport=None):
        self.calls.append(("connect", str(address), transport))
        return FakeLink(str(address), self.calls)

    async def accept(self, *args, **kwargs):
        return FakeLink(self.incoming, self.calls)


def test_when_both_connections_succeed_the_losing_one_is_disconnected():
    radio = BothConnectRadio(incoming=AIRPODS)
    link = paired(radio, address=AIRPODS)
    assert sum(1 for c in radio.calls if c[0] == "connect") == 1
    disconnected = [c[1] for c in radio.calls if c[0] == "disconnect"]
    assert len(disconnected) == 1 and disconnected[0] is not link
