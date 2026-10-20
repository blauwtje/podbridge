import asyncio
import logging
from types import SimpleNamespace

import pytest
from bumble import avdtp, l2cap

from podbridge import host
from test_host import tone


class Channel:
    def __init__(self):
        self.sent = []

    def write(self, sdu):
        self.sent.append(bytes(sdu))


class Remote:
    """The peer's endpoint proxy: counts START commands; the first `fail_starts` after the initial one fail.

    While `gate` is set, a START after the initial one waits for it.
    """

    def __init__(self):
        self.seid = 2
        self.starts, self.fail_starts, self.gate = 0, 0, None

    async def start(self):
        self.starts += 1
        if self.starts == 1:
            return
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_starts:
            self.fail_starts -= 1
            raise RuntimeError("peer refused START")


class RealProtocol:
    """An AVDTP signaling channel whose source and stream are Bumble's own, so its SUSPEND/START handlers run."""

    def __init__(self):
        self.l2cap_channel = SimpleNamespace(state=l2cap.ClassicChannel.State.OPEN)
        self.remote, self.rtp = Remote(), Channel()
        self.stream = None

    async def discover_remote_endpoints(self):
        pass

    def find_remote_sink_by_codec(self, media_type, codec_type):
        return codec_type

    def add_source(self, capabilities, pump):
        return avdtp.LocalSource(self, 1, capabilities, [], pump)

    async def create_stream(self, local, sink):
        self.stream = avdtp.Stream(self, local, self.remote)
        self.stream.rtp_channel = self.rtp
        self.stream.state = avdtp.State.OPEN
        return self.stream


def _peer(monkeypatch, delay=0.02, retry=0.02):
    protocol = RealProtocol()

    async def find(connection):
        return (1, 3)

    async def connect(connection, version):
        return protocol

    monkeypatch.setattr(avdtp, "find_avdtp_service_with_connection", find)
    monkeypatch.setattr(avdtp.Protocol, "connect", connect)
    monkeypatch.setattr(host, "RESTART_DELAY_S", delay, raising=False)
    monkeypatch.setattr(host, "RESTART_RETRY_S", retry, raising=False)
    return protocol


async def wait_until(condition, timeout=2.0):
    """Poll `condition` every 5 ms; fail the test when it is still false after `timeout`."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


async def _streaming(h):
    """Start the stream and wait until its pump has sent a packet."""
    h.source.feed(tone(0.5))
    stream = await h.stream("acl")
    await wait_until(lambda: h.source.sent > 0)
    return stream


async def _stop_pump(stream):
    await asyncio.sleep(0)  # a pump task canceled before its first step makes Bumble's stop() raise
    await stream.local_endpoint.packet_pump.stop()


async def _finished(h):
    """Wait for the restart task of the current stream to end."""
    await wait_until(lambda: h._restarter.task is not None)
    await asyncio.wait_for(h._restarter.task, 2.0)


def test_peer_suspend_is_followed_by_a_start_and_the_pump_sends_again(monkeypatch, caplog):
    protocol = _peer(monkeypatch)
    caplog.set_level(logging.INFO, logger="podbridge")

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await _streaming(h)
        await stream.on_suspend_command()  # the AirPods' AVDTP SUSPEND
        assert stream.state == avdtp.State.OPEN
        starts, sent = protocol.remote.starts, h.source.sent
        await wait_until(lambda: stream.state == avdtp.State.STREAMING)
        h.source.feed(tone(0.5))
        await wait_until(lambda: h.source.sent > sent)
        await _finished(h)
        await _stop_pump(stream)
        return protocol.remote.starts - starts

    assert asyncio.run(main()) == 1
    assert "suspended by the peer" in caplog.text
    assert "A2DP stream restarted after peer SUSPEND (aac)" in caplog.text


def test_a_failing_restart_is_logged_as_a_warning_and_retried(monkeypatch, caplog):
    protocol = _peer(monkeypatch)
    caplog.set_level(logging.INFO, logger="podbridge")

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await _streaming(h)
        protocol.remote.fail_starts = 2
        await stream.on_suspend_command()
        await wait_until(lambda: stream.state == avdtp.State.STREAMING)
        await _finished(h)
        await _stop_pump(stream)

    asyncio.run(main())
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2 and "peer refused START" in warnings[0].getMessage()
    assert protocol.remote.starts == 4


def test_the_restart_gives_up_after_three_starts_and_the_next_suspend_arms_it_again(monkeypatch, caplog):
    protocol = _peer(monkeypatch)
    caplog.set_level(logging.INFO, logger="podbridge")

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await _streaming(h)
        protocol.remote.fail_starts = 99
        await stream.on_suspend_command()
        await _finished(h)
        gave_up = (protocol.remote.starts, stream.state)
        protocol.remote.fail_starts = 0
        await stream.local_endpoint.on_suspend_command()  # the stream stays OPEN, so only the handler is called
        await wait_until(lambda: stream.state == avdtp.State.STREAMING)
        await _finished(h)
        await _stop_pump(stream)
        return gave_up, protocol.remote.starts

    gave_up, starts = asyncio.run(main())
    assert gave_up == (4, avdtp.State.OPEN)
    assert "A2DP restart gave up after 3 attempts (aac): RuntimeError('peer refused START')" in caplog.text
    assert starts == 5


def test_no_restart_when_the_peer_started_the_stream_again_first(monkeypatch, caplog):
    protocol = _peer(monkeypatch, delay=0.05)
    caplog.set_level(logging.INFO, logger="podbridge")

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await _streaming(h)
        await stream.on_suspend_command()
        await stream.on_start_command()  # the peer's own START
        await _finished(h)
        await _stop_pump(stream)
        return stream.state

    state = asyncio.run(main())
    assert state == avdtp.State.STREAMING and protocol.remote.starts == 1
    assert "started again by the peer" in caplog.text
    assert "restarted after peer SUSPEND" not in caplog.text


def test_a_peer_start_during_the_restart_leaves_one_pump(monkeypatch):
    protocol = _peer(monkeypatch)

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await _streaming(h)
        pump = stream.local_endpoint.packet_pump
        await stream.on_suspend_command()
        protocol.remote.gate = asyncio.Event()
        await wait_until(lambda: protocol.remote.starts == 2)  # the restart waits for the peer's START response
        await stream.on_start_command()  # the peer's own START lands first
        first = pump.pump_task
        protocol.remote.gate.set()
        await _finished(h)
        same = pump.pump_task is first and not first.done()
        await _stop_pump(stream)
        return same

    assert asyncio.run(main()), "a second pump replaced the running one"


def test_a_failing_pump_stop_on_suspend_still_schedules_the_restart(monkeypatch):
    _peer(monkeypatch)

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await _streaming(h)
        pump = stream.local_endpoint.packet_pump
        stop = pump.stop

        async def failing_stop():
            raise RuntimeError("pump stop failed")

        pump.stop = failing_stop
        with pytest.raises(RuntimeError, match="pump stop failed"):
            await stream.on_suspend_command()
        await _finished(h)
        await asyncio.sleep(0)
        await stop()

    asyncio.run(main())


def test_no_restart_once_the_signaling_channel_is_closed(monkeypatch):
    protocol = _peer(monkeypatch)

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await _streaming(h)
        await stream.on_suspend_command()
        protocol.l2cap_channel.state = l2cap.ClassicChannel.State.CLOSED
        await _finished(h)
        await _stop_pump(stream)
        return stream.state

    assert asyncio.run(main()) == avdtp.State.OPEN and protocol.remote.starts == 1


def test_close_and_a_new_start_cancel_the_waiting_restart(monkeypatch):
    protocol = _peer(monkeypatch, delay=30.0)

    async def main():
        h = host.Host("usb:0", codec="aac")
        stream = await _streaming(h)
        await stream.on_suspend_command()
        first = h._restarter.task
        await _stop_pump(stream)
        await h._start(protocol)  # what the AAC to SBC fallback does
        replaced = first.cancelled()
        await h.stream("acl")
        await h._start(protocol)
        await h.close()
        return replaced, h._restarter

    replaced, restarter = asyncio.run(main())
    assert replaced and restarter is None
