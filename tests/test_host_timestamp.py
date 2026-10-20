import asyncio
import struct

import pytest
from bumble import avdtp

from podbridge import host
from podbridge.a2dp_encoder import A2dpEncoder
from test_host import tone


class Channel:
    def __init__(self):
        self.sent = []

    def write(self, sdu):
        self.sent.append(bytes(sdu))


@pytest.fixture(autouse=True)
def fast_clock(monkeypatch):
    """Bumble's pump paces on timestamp_seconds; run its clock virtually so the test needs no real seconds."""
    now = [1000.0]

    async def sleep(self, duration):
        now[0] += duration
        await asyncio.sleep(0)

    monkeypatch.setattr(avdtp.RealtimeClock, "now", lambda self: now[0])
    monkeypatch.setattr(avdtp.RealtimeClock, "sleep", sleep)


def rtp_timestamps(sdus):
    """The RTP timestamp field (bytes 4-8, RFC 3550) of each SDU the peer received."""
    return [struct.unpack(">I", sdu[4:8])[0] for sdu in sdus]


async def pump_packets(codec, first_sample=0, seconds=0.5):
    source = host.EncodedPacketSource(A2dpEncoder(codec))
    source._samples = first_sample  # only way to reach the 32-bit wrap without 2**32 samples of audio
    pump = avdtp.MediaPacketPump(source.packets)
    channel = Channel()
    await pump.start(channel)
    for _ in range(int(seconds * 10)):
        source.feed(tone(0.1))
        await asyncio.sleep(0.01)
    await pump.stop()
    return channel.sent


def test_aac_rtp_timestamp_advances_1024_samples_per_frame():
    timestamps = rtp_timestamps(asyncio.run(pump_packets("aac")))
    assert len(timestamps) > 10
    assert [b - a for a, b in zip(timestamps, timestamps[1:])] == [1024] * (len(timestamps) - 1)


def test_sbc_rtp_timestamp_advances_by_the_samples_of_each_packet():
    timestamps = rtp_timestamps(asyncio.run(pump_packets("sbc")))
    assert len(timestamps) > 10
    assert [b - a for a, b in zip(timestamps, timestamps[1:])] == [128] * (len(timestamps) - 1)


def test_rtp_timestamp_wraps_at_32_bits():
    timestamps = rtp_timestamps(asyncio.run(pump_packets("aac", first_sample=2**32 - 3 * 1024)))
    assert timestamps[:4] == [2**32 - 3 * 1024, 2**32 - 2 * 1024, 2**32 - 1024, 0]
