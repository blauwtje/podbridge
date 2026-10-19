"""AAP mic session: open the channel, send the start sequence, queue access units (Decision 9)."""

import asyncio
import time

from bumble import l2cap

from podbridge import aap

SETTLE_S = 1.5
OPEN_RETRIES = 3
OPEN_RETRY_S = 3.0
COMMAND_GAP_S = 0.3
STALL_S = 2.0
RESTART_MIN_S = 5.0
MTU = 4096
POLL_S = 0.25

_SEQUENCE = (aap.HANDSHAKE, aap.FEATURES, aap.NOTIFICATIONS, aap.CONVERSATIONAL_AWARENESS_OFF, aap.START)


class AapSession:
    """Puts each mic access unit on `queue`; never sends STOP."""

    def __init__(self, connection, queue: asyncio.Queue, clock=time.monotonic):
        self.connection = connection
        self.queue = queue
        self.clock = clock
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

    def _on_sdu(self, sdu: bytes) -> None:
        for unit in aap.parse_mic_sdu(sdu):
            self._last_frame = self.clock()
            self.queue.put_nowait(unit)

    async def run(self) -> None:
        """Run until cancelled or the channel fails to open."""
        await asyncio.sleep(SETTLE_S)
        channel = await self._open()
        channel.sink = self._on_sdu
        for index, command in enumerate(_SEQUENCE):
            if index:
                await asyncio.sleep(COMMAND_GAP_S)
            channel.write(command)
        self._last_start = self._last_frame = self.clock()
        while True:
            await asyncio.sleep(POLL_S)
            now = self.clock()
            if now - self._last_frame >= STALL_S and now - self._last_start >= RESTART_MIN_S:
                channel.write(aap.START)
                self._last_start = now
                self.restarts += 1
