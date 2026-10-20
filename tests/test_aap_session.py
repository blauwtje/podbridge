import asyncio
import struct

from bumble import core, l2cap
from bumble.controller import Controller
from bumble.device import Device
from bumble.host import Host
from bumble.link import LocalLink

from podbridge import aap, aap_inbound, aap_session
from podbridge.aap_session import AapSession


def mic_sdu(*units: bytes) -> bytes:
    body = b"".join(struct.pack("<IB", 0, len(u)) + u for u in units)
    return bytes.fromhex("04000400580001000000000000000000000000000000") + body


def make_device(name, address, link):
    controller = Controller(name, link=link, public_address=address)
    device = Device(name=name, address=address, host=Host(controller, controller))
    device.classic_enabled = True
    return device


def fast(monkeypatch):
    for name, value in dict(SETTLE_S=0.01, OPEN_RETRY_S=0.01, COMMAND_GAP_S=0.01, STALL_S=0.2, RESTART_MIN_S=0.4, POLL_S=0.02).items():
        monkeypatch.setattr(aap_session, name, value)


def test_session_sends_sequence_queues_units_and_restarts_start_after_stall(monkeypatch):
    fast(monkeypatch)
    assert aap.is_mic_sdu(mic_sdu(b"abc"))
    assert list(aap.parse_mic_sdu(mic_sdu(b"abc", b"de"))) == [b"abc", b"de"]

    async def scenario():
        link = LocalLink()
        pod = make_device("pod", "F0:F1:F2:F3:F4:01", link)
        pc = make_device("pc", "F0:F1:F2:F3:F4:02", link)
        await pod.power_on()
        await pc.power_on()
        received, channels = [], []

        def on_channel(channel):
            channel.sink = received.append
            channels.append(channel)

        pod.create_l2cap_server(l2cap.ClassicChannelSpec(psm=aap.PSM), on_channel)
        connection = await pc.connect("F0:F1:F2:F3:F4:01", transport=core.PhysicalTransport.BR_EDR)
        queue = asyncio.Queue()
        session = AapSession(connection, queue)
        task = asyncio.create_task(session.run())
        await asyncio.wait_for(_until(lambda: len(received) >= 5), 5)
        assert received[:5] == [aap.HANDSHAKE, aap.FEATURES, aap.NOTIFICATIONS, aap.CONVERSATIONAL_AWARENESS_OFF, aap.START]
        channels[0].write(mic_sdu(b"abc", b"de"))
        assert [await asyncio.wait_for(queue.get(), 2) for _ in range(2)] == [b"abc", b"de"]
        await asyncio.wait_for(_until(lambda: session.restarts >= 1), 5)
        assert received[5:] == [aap.START] * len(received[5:])
        assert aap.STOP not in received
        task.cancel()

    asyncio.run(scenario())


async def _until(predicate):
    while not predicate():
        await asyncio.sleep(0.01)


def test_open_retries_then_succeeds(monkeypatch):
    fast(monkeypatch)
    attempts = []

    class Channel:
        def __init__(self):
            self.sent = []

        def write(self, data):
            self.sent.append(data)

    channel = Channel()

    class Connection:
        async def create_l2cap_channel(self, spec):
            attempts.append(spec.psm)
            if len(attempts) < 3:
                raise ConnectionError("refused")
            return channel

    async def scenario():
        task = asyncio.create_task(AapSession(Connection(), asyncio.Queue()).run())
        await asyncio.wait_for(_until(lambda: len(channel.sent) == 5), 5)
        task.cancel()

    asyncio.run(scenario())
    assert attempts == [aap.PSM] * 3


def test_routes_events_sends_writes_and_ca_choice(monkeypatch):
    fast(monkeypatch)
    sent, events = [], []

    class Channel:
        sink = None

        def write(self, data):
            sent.append(data)

    channel = Channel()

    class Connection:
        async def create_l2cap_channel(self, spec):
            return channel

    battery = bytes.fromhex("040004000400") + bytes([1, 1, 0, 80, 1, 0])
    ctl = aap.control(0x01, 2)

    async def scenario():
        session = AapSession(Connection(), asyncio.Queue(), on_event=events.append, ca_choice=1)
        task = asyncio.create_task(session.run())
        await asyncio.wait_for(_until(lambda: len(sent) == 5), 5)
        assert sent[3] == aap.control(0x28, 1)
        channel.sink(battery)
        channel.sink(ctl)
        channel.sink(b"\x00junk")
        session.send(aap.control(0x0D, 2))
        await asyncio.wait_for(_until(lambda: len(sent) == 6), 5)
        task.cancel()
        assert sent[5] == aap.control(0x0D, 2)

    asyncio.run(scenario())
    assert events[0] == aap_inbound.Battery(1, 80, 1)
    assert events[1] == (0x01, bytes([2, 0, 0, 0]))
    assert len(events) == 2
