import asyncio

import pytest

from podbridge import host
from podbridge.a2dp_encoder import AAC_FRAME_SAMPLES, AAC_PACKET_OVERHEAD, SAMPLE_RATE, A2dpEncoder, aac_bit_rate_for_mtu
from test_host import tone


def test_a_pump_start_drops_the_backlog_queued_before_it():
    # why: Bumble paces packets from the first one sent, so a backlog at start stays as delay for the whole stream
    async def run():
        source = host.EncodedPacketSource(A2dpEncoder("aac"))
        source.feed(tone(2))
        assert source._queue.qsize() > 0
        first = asyncio.ensure_future(anext(aiter(source.packets)))
        await asyncio.sleep(0)
        backlog_sent = first.done()
        first.cancel()
        return backlog_sent, source._queue.qsize()

    backlog_sent, queued = asyncio.run(run())
    assert not backlog_sent
    assert queued == 0


@pytest.mark.parametrize("codec", ["aac", "sbc"])
def test_the_packet_queue_holds_at_most_200_ms_of_audio(codec):
    encoder = A2dpEncoder(codec)
    seconds = host.EncodedPacketSource(encoder)._queue.maxsize * host.frame_samples(codec) / encoder.rate
    assert 0.1 <= seconds <= 0.2


def test_the_aac_bit_rate_for_mtu_1023_fits_the_peak_frame_seen_in_a_real_run():
    # real run at 256 kbps: mean frame 683 bytes, largest packet 1256 bytes, so peak payload over mean is 1.79
    mean = aac_bit_rate_for_mtu(1023) * AAC_FRAME_SAMPLES / SAMPLE_RATE / 8
    assert mean * 1.79 + AAC_PACKET_OVERHEAD <= 1023


def test_the_aac_bit_rate_for_mtu_1023_also_fits_the_2_02_peak_of_a_second_real_run():
    # 195592 bit/s run: a 1059 byte packet over the peer MTU 1023, peak over mean 2.02
    mean = aac_bit_rate_for_mtu(1023) * AAC_FRAME_SAMPLES / SAMPLE_RATE / 8
    assert mean * 2.02 + AAC_PACKET_OVERHEAD <= 1023
