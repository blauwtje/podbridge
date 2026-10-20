import asyncio

from bumble import avdtp
from pyee import EventEmitter

from podbridge.a2dp_encoder import A2dpEncoder
from podbridge.host import EncodedPacketSource
from test_host import tone


class Channel:
    def __init__(self):
        self.sent = []

    def write(self, sdu):
        self.sent.append(bytes(sdu))


def test_pump_sends_again_after_suspend_then_start():
    async def main():
        source = EncodedPacketSource(A2dpEncoder("aac"))
        pump = avdtp.MediaPacketPump(source.packets)
        channel = Channel()

        async def feed_for(seconds):
            for _ in range(int(seconds * 10)):
                source.feed(tone(0.1))
                await asyncio.sleep(0.1)

        await pump.start(channel)
        await feed_for(0.5)
        before = len(channel.sent)
        await pump.stop()  # LocalSource.on_suspend_command
        await pump.start(channel)  # LocalSource.on_start_command
        await feed_for(0.5)
        await pump.stop()
        return before, len(channel.sent)

    before, total = asyncio.run(main())
    assert before > 0
    assert total - before > 0, "no packets after SUSPEND then START"


def test_suspend_and_start_commands_are_logged_and_only_suspend_schedules_the_restart(caplog, monkeypatch):
    import logging

    from podbridge import host

    scheduled = []
    monkeypatch.setattr(host.StreamRestarter, "schedule", lambda self: scheduled.append(self))
    caplog.set_level(logging.INFO, logger="podbridge")
    local = avdtp.LocalSource.__new__(avdtp.LocalSource)
    EventEmitter.__init__(local)
    local.packet_pump = None
    host.handle_peer_commands(local, "aac")
    asyncio.run(local.on_start_command())
    assert scheduled == [] and "started again" in caplog.text
    asyncio.run(local.on_suspend_command())
    assert len(scheduled) == 1 and "suspended" in caplog.text
