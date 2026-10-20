import asyncio
import logging
import random

from bumble import avdtp

from podbridge import host
from podbridge.a2dp_encoder import A2dpEncoder
from test_host_mtu import fast_clock, loud_music, stream_music  # noqa: F401  (fast_clock is an autouse fixture)
from test_host_restart import _peer, _stop_pump


class Channel:
    def __init__(self):
        self.sent = []

    def write(self, sdu):
        self.sent.append(bytes(sdu))


async def play(source, seconds, seed):
    """Feed `seconds` of loud audio through a running pump and return the SDUs it wrote."""
    pump = avdtp.MediaPacketPump(source.packets)
    channel, rng = Channel(), random.Random(seed)
    await pump.start(channel)
    for _ in range(int(seconds * 10)):
        source.feed(loud_music(0.1, rng))
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)
    await pump.stop()
    return channel.sent


def test_packets_queued_before_the_bit_rate_cap_are_cleared_not_sent_over_it():
    async def main():
        source = host.EncodedPacketSource(A2dpEncoder("aac"))
        rng = random.Random(1)
        for _ in range(5):
            source.feed(loud_music(0.1, rng))  # queued at 256 kbps, no pump running yet
        source.set_peer_mtu(672)
        sent = await play(source, 1.0, seed=2)
        return source, sent

    source, sent = asyncio.run(main())
    assert sent and max(len(sdu) for sdu in sent) <= 672
    assert source.oversize == 0


def test_a_later_start_with_no_mtu_resets_the_cap_and_the_oversize_check():
    async def main():
        source = host.EncodedPacketSource(A2dpEncoder("aac"))
        source.set_peer_mtu(672)
        source.set_peer_mtu(None)
        sent = await play(source, 1.0, seed=2)
        return source, sent

    source, sent = asyncio.run(main())
    assert max(len(sdu) for sdu in sent) > 672, "the default 256 kbps rate is back"
    assert source.oversize == 0, "no MTU is known, so nothing is oversize"


def test_host_start_without_an_mtu_after_one_with_an_mtu_uses_the_default_rate(monkeypatch):
    protocol = _peer(monkeypatch)
    protocol.rtp.peer_mtu = 672

    async def main():
        h = host.Host("usb:0", codec="aac")
        await _stop_pump(await stream_music(h, seconds=0.5))
        before = len(protocol.rtp.sent)
        del protocol.rtp.peer_mtu  # the next peer reports no MTU
        await _stop_pump(await stream_music(h, seconds=1.0))
        return h, protocol.rtp.sent[before:]

    h, later = asyncio.run(main())
    assert max(len(sdu) for sdu in later) > 672
    assert h.source.oversize == 0


def test_the_oversize_warning_fires_once_per_mtu_reading_and_the_counter_keeps_the_total(caplog):
    caplog.set_level(logging.WARNING, logger="podbridge")

    async def main():
        source = host.EncodedPacketSource(A2dpEncoder("aac"))
        source.set_peer_mtu(100)
        await play(source, 1.0, seed=1)
        first = source.oversize
        source.set_peer_mtu(120)
        await play(source, 1.0, seed=2)
        return source, first

    source, first = asyncio.run(main())
    assert caplog.text.count("A2DP packet of") == 2
    assert "MTU 100" in caplog.text and "MTU 120" in caplog.text
    assert first > 1 and source.oversize > first


def frame(payload_bytes):
    """An ADTS frame whose RTP packet carries `payload_bytes` of AAC (the pump strips the 7-byte header)."""
    return bytes(7) + bytes(payload_bytes)


async def pump_frames(source, frames):
    """Run the packet generator, queue `frames` after its start, and return the packets it yields."""
    stream = source.packets.__aiter__()
    first = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0)  # the generator has cleared the queue and waits for a frame

    async def feeder():
        for f in frames:
            await source._queue.put(f)  # waits when the queue is full

    feed = asyncio.ensure_future(feeder())
    out = []
    pending = first
    try:
        while True:
            out.append(await asyncio.wait_for(pending, 0.05))
            pending = asyncio.ensure_future(stream.__anext__())
    except asyncio.TimeoutError:
        pass
    await feed
    return out


def test_a_single_oversize_frame_is_dropped_with_no_sequence_gap_and_a_timestamp_gap(caplog):
    caplog.set_level(logging.WARNING, logger="podbridge")

    async def main():
        source = host.EncodedPacketSource(A2dpEncoder("aac"))
        source.set_peer_mtu(1023)
        packets = await pump_frames(source, [frame(900), frame(1100), frame(900)])
        return source, packets

    source, packets = asyncio.run(main())
    assert len(packets) == 2
    assert all(len(bytes(p)) <= 1023 for p in packets)
    assert packets[1].sequence_number == packets[0].sequence_number + 1
    assert packets[1].timestamp == packets[0].timestamp + 2048
    assert source.oversize == 1 and source.dropped == 1 and source.sent == 2
    assert caplog.text.count("exceeds the peer MTU 1023; dropped") == 1


def test_after_8_oversize_packets_in_a_row_the_9th_is_sent_with_a_warning_and_a_fit_ends_it(caplog):
    caplog.set_level(logging.WARNING, logger="podbridge")

    async def main():
        source = host.EncodedPacketSource(A2dpEncoder("aac"))
        source.set_peer_mtu(1023)
        # 8 dropped, the 9th and 10th sent anyway, a fitting one, then oversize packets are dropped again
        frames = [frame(1100)] * 10 + [frame(900)] + [frame(1100)] * 2
        packets = await pump_frames(source, frames)
        return source, packets

    source, packets = asyncio.run(main())
    sizes = [len(bytes(p)) for p in packets]
    assert [size > 1023 for size in sizes] == [True, True, False], "the 9th is sent oversize, a fit ends the pass-through"
    assert packets[0].sequence_number == 0 and packets[0].timestamp == 8 * 1024
    assert source.dropped == 10 and source.oversize == 12 and source.sent == 3
    assert caplog.text.count("every packet exceeds the peer MTU 1023; sending them anyway") == 1
    assert caplog.text.count("exceeds the peer MTU 1023; dropped") == 1
