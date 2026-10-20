import asyncio
import logging
import random
import re
import struct

import pytest
from bumble import avdtp

from podbridge import host
from test_host_restart import _peer, _stop_pump, wait_until

SAMPLE_RATE = 48000


@pytest.fixture(autouse=True)
def fast_clock(monkeypatch):
    """Bumble's pump paces on timestamp_seconds; run its clock virtually so the test needs no real seconds."""
    now = [1000.0]

    async def sleep(self, duration):
        now[0] += duration
        await asyncio.sleep(0)

    monkeypatch.setattr(avdtp.RealtimeClock, "now", lambda self: now[0])
    monkeypatch.setattr(avdtp.RealtimeClock, "sleep", sleep)
    monkeypatch.setattr(host, "STATS_S", 0.0)


def loud_music(seconds: float, rng: random.Random) -> bytes:
    """A tone plus noise: the AAC encoder needs its full bit rate, as the 256 kbps packets in the real run did."""
    out = bytearray()
    for i in range(int(seconds * SAMPLE_RATE)):
        v = 0.4 * rng.uniform(-1, 1) + 0.3 * rng.uniform(-1, 1)
        s = int(v * 32767)
        out += struct.pack("<hh", s, s)
    return bytes(out)


async def stream_music(h, seconds=3.0):
    """Start the stream and feed `seconds` of loud audio while its pump runs."""
    rng = random.Random(1)
    stream = await h.stream("acl")
    for _ in range(int(seconds * 10)):
        h.source.feed(loud_music(0.1, rng))
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)
    return stream


def stats(caplog):
    """(max_packet, oversize) of each "A2DP packets:" line."""
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("A2DP packets:")]
    return [(int(m["max"]), int(m["oversize"])) for line in lines if (m := re.search(r"max_packet=(?P<max>\d+).*oversize=(?P<oversize>\d+)", line))]


@pytest.mark.parametrize("mtu", [672, 335])
def test_every_aac_packet_fits_the_peer_media_mtu_and_none_is_oversize(monkeypatch, caplog, mtu):
    protocol = _peer(monkeypatch)
    protocol.rtp.peer_mtu = mtu
    caplog.set_level(logging.INFO, logger="podbridge")

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await stream_music(h)
        await _stop_pump(stream)

    asyncio.run(main())
    sizes = [len(sdu) for sdu in protocol.rtp.sent]
    assert max(sizes) <= mtu, f"largest SDU {max(sizes)} B exceeds the peer MTU {mtu}"
    assert len(sizes) >= 100, "the bit rate is capped so the frames fit, not the audio oversize"
    seen = stats(caplog)
    assert seen and all(size <= mtu for size, _ in seen) and max(size for size, _ in seen) > mtu // 2
    assert all(oversize == 0 for _, oversize in seen)


def test_a_packet_that_cannot_fit_the_peer_mtu_is_dropped_counted_and_warned_once(monkeypatch, caplog):
    protocol = _peer(monkeypatch)
    protocol.rtp.peer_mtu = 100  # below any AAC packet at the lowest bit rate
    caplog.set_level(logging.INFO, logger="podbridge")

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await stream_music(h, seconds=1.0)
        await _stop_pump(stream)

    asyncio.run(main())
    assert 20 <= len(protocol.rtp.sent) < 46, "the first 8 are dropped, then a wrong MTU reading must not silence the stream"
    assert stats(caplog)[-1][1] > 0, "oversize packets are counted in the stats line"
    assert re.search(r"dropped=8\b", [r.getMessage() for r in caplog.records if r.getMessage().startswith("A2DP packets:")][-1])
    assert caplog.text.count("A2DP packet of") == 1
    assert caplog.text.count("exceeds the peer MTU 100; dropped") == 1
    assert caplog.text.count("every packet exceeds the peer MTU 100; sending them anyway") == 1


def test_the_peer_media_mtu_is_logged_once_at_stream_start(monkeypatch, caplog):
    protocol = _peer(monkeypatch)
    protocol.rtp.peer_mtu = 672
    caplog.set_level(logging.INFO, logger="podbridge")

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await stream_music(h, seconds=1.0)
        await _stop_pump(stream)

    asyncio.run(main())
    assert caplog.text.count("peer MTU=672") == 1


def test_the_stats_max_packet_size_restarts_after_each_stats_line(monkeypatch, caplog):
    protocol = _peer(monkeypatch)
    protocol.rtp.peer_mtu = 672
    caplog.set_level(logging.INFO, logger="podbridge")

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await stream_music(h, seconds=1.0)
        await _stop_pump(stream)
        h.source.feed(loud_music(0.1, random.Random(2)))  # reports what the pump sent before
        h.source.feed(loud_music(0.1, random.Random(3)))  # nothing was sent since the last line

    asyncio.run(main())
    assert stats(caplog)[-1][0] == 0


def test_the_media_channel_is_opened_first_so_its_mtu_is_known_when_the_stream_is_configured(monkeypatch, caplog):
    protocol = _peer(monkeypatch)
    caplog.set_level(logging.INFO, logger="podbridge")
    create_stream = protocol.create_stream

    async def configured_stream(local, sink):
        stream = await create_stream(local, sink)
        stream.rtp_channel, stream.state = None, avdtp.State.CONFIGURED  # Bumble opens the channel in start()
        return stream

    async def open_stream(self):  # what Stream.open() does once the L2CAP channel is configured
        self.rtp_channel, self.state = protocol.rtp, avdtp.State.OPEN
        protocol.rtp.peer_mtu = 672

    protocol.create_stream = configured_stream
    monkeypatch.setattr(avdtp.Stream, "open", open_stream)

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await stream_music(h, seconds=1.0)
        await _stop_pump(stream)

    asyncio.run(main())
    assert "peer MTU=672" in caplog.text
    assert max(len(sdu) for sdu in protocol.rtp.sent) <= 672


def test_peer_mtu_48_means_no_mtu_option_so_672_is_assumed_and_the_stream_still_flows(monkeypatch, caplog):
    protocol = _peer(monkeypatch)
    protocol.rtp.peer_mtu = 48  # Bumble's initial value when the peer sent no MTU option
    caplog.set_level(logging.INFO, logger="podbridge")

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await stream_music(h)
        await _stop_pump(stream)

    asyncio.run(main())
    sizes = [len(sdu) for sdu in protocol.rtp.sent]
    assert len(sizes) >= 100, "packets still flow"
    assert max(sizes) <= 672, "the cap uses 672"
    assert max(sizes) > 335, "the cap is 672, not a smaller MTU"
    assert "assuming 672" in caplog.text
