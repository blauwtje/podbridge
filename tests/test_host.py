import asyncio
import math
import os
import struct

import pytest
from bumble import avdtp, core, sdp
from bumble.keys import PairingKeys

from podbridge import host, paths
from podbridge.a2dp_encoder import A2dpEncoder

HFP_HSP = {0x1108, 0x1112, 0x111E, 0x111F}


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
        gen = source.packets
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
