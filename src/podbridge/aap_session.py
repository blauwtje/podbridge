"""AAP mic session: open the channel, send the start sequence, queue access units (Decision 9)."""

import asyncio
import time

from bumble import l2cap

from podbridge import aap, aap_inbound

SETTLE_S = 1.5
OPEN_RETRIES = 3
OPEN_RETRY_S = 3.0
COMMAND_GAP_S = 0.3
STALL_S = 2.0
RESTART_MIN_S = 5.0
MTU = 4096
POLL_S = 0.25



class AapSession:
    """Puts each mic access unit on `queue`; never sends STOP.

    Other inbound frames reach `on_event` as aap_inbound events or (setting id, data) tuples.
    Options: clock, on_event, ca_choice (keywords). Frames passed to `send` go out from `run`'s loop.
    """

    def __init__(self, connection, queue: asyncio.Queue, **options):
        self.connection = connection
        self.queue = queue
        self.clock = options.get("clock", time.monotonic)
        self.on_event = options.get("on_event")
        self.ca_choice = options.get("ca_choice")
        self.outgoing: asyncio.Queue = asyncio.Queue()
        self.restarts = 0
        self._last_frame = 0.0
        self._last_start = 0.0

    async def _try_open(self):
        try:
            return await self.connection.create_l2cap_channel(l2cap.ClassicChannelSpec(psm=aap.PSM, mtu=MTU))
        except Exception:
            return None

    async def _open(self):
        for _ in range(OPEN_RETRIES - 1):
            channel = await self._try_open()
            if channel is not None:
                return channel
            await asyncio.sleep(OPEN_RETRY_S)
        return await self.connection.create_l2cap_channel(l2cap.ClassicChannelSpec(psm=aap.PSM, mtu=MTU))

    def send(self, frame: bytes) -> None:
        self.outgoing.put_nowait(frame)

    def _on_sdu(self, sdu: bytes) -> None:
        if not aap.is_mic_sdu(sdu):
            self._on_control(sdu)
            return
        for unit in aap.parse_mic_sdu(sdu):
            self._last_frame = self.clock()
            self.queue.put_nowait(unit)

    def _on_control(self, frame: bytes) -> None:
        if self.on_event is None:
            return
        events = aap_inbound.parse(frame)
        if events is None:
            control = aap.parse_control(frame)
            events = [control] if control else []
        for event in events:
            self.on_event(event)

    async def run(self) -> None:
        """Run until cancelled or the channel fails to open."""
        await asyncio.sleep(SETTLE_S)
        channel = await self._open()
        channel.sink = self._on_sdu
        ca = aap.control(0x28, self.ca_choice) if self.ca_choice is not None else aap.CONVERSATIONAL_AWARENESS_OFF
        sequence = (aap.HANDSHAKE, aap.FEATURES, aap.NOTIFICATIONS, ca, aap.START)
        for index, command in enumerate(sequence):
            if index:
                await asyncio.sleep(COMMAND_GAP_S)
            channel.write(command)
        self._last_start = self._last_frame = self.clock()
        while True:
            await asyncio.sleep(POLL_S)
            while not self.outgoing.empty():
                channel.write(self.outgoing.get_nowait())
            now = self.clock()
            if now - self._last_frame >= STALL_S and now - self._last_start >= RESTART_MIN_S:
                channel.write(aap.START)
                self._last_start = now
                self.restarts += 1
