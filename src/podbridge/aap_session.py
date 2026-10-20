"""AAP mic session: open the channel, send the start sequence, queue access units (Decision 9)."""

import asyncio
import logging
import time

from bumble import l2cap
from bumble.core import BaseBumbleError

from podbridge import aap, aap_inbound
from podbridge.events import Event

log = logging.getLogger("podbridge")
SETTLE_S = 1.5
DROP_LOG_FIRST = 50
DROP_COUNT_S = 30.0
OPEN_RETRIES = 3
OPEN_RETRY_S = 3.0
COMMAND_GAP_S = 0.3
STALL_S = 2.0
RESTART_MIN_S = 5.0
MTU = 4096
POLL_S = 0.25
AAP_RETRY_S = 10.0
AAP_GIVE_UP = 6
UNAVAILABLE = "mic unavailable, audio output only"
MIC_SILENT_RESTARTS = 3
MIC_SILENT = "The AirPods send no mic audio; switch to the AirPods, run podbridge doctor"


class AapSession:
    """Puts each mic access unit on `queue`; never sends STOP.

    Other inbound frames reach `on_event` as aap_inbound events or, for a setting echo, the control frame bytes.
    Options: clock, on_event, ca_choice, reporter (keywords); a failing channel is retried, never raised. Frames passed to `send` go out from `run`'s loop.
    """

    def __init__(self, connection, queue: asyncio.Queue, **options):
        self.connection = connection
        self.queue = queue
        self.clock = options.get("clock", time.monotonic)
        self.on_event = options.get("on_event")
        self.ca_choice = options.get("ca_choice")
        self.reporter = options.get("reporter")
        self.outgoing: asyncio.Queue = asyncio.Queue()
        self.started = asyncio.Event()
        self.restarts = 0
        self.frames_seen = 0
        self._silent_reported = False
        self._last_frame = 0.0
        self._last_start = 0.0
        self._drops = 0
        self._drop_count_at = 0.0

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
            self.frames_seen += 1
            self.queue.put_nowait(unit)

    def _dropped(self, frame: bytes) -> None:
        """Log an SDU nothing consumed: the first DROP_LOG_FIRST as hex, then a count every DROP_COUNT_S."""
        self._drops += 1
        if self._drops <= DROP_LOG_FIRST:
            log.info("dropped inbound SDU %s", frame.hex())
            return
        now = self.clock()
        if now - self._drop_count_at >= DROP_COUNT_S:
            self._drop_count_at = now
            log.info("dropped inbound SDUs so far: %d", self._drops)

    def _on_control(self, frame: bytes) -> None:
        if frame[4:6] == b"\x09\x00":
            log.info("control frame %s", frame.hex())
        if self.on_event is None:
            self._dropped(frame)
            return
        events = aap_inbound.parse(frame)
        if events is None:
            events = [frame] if aap.parse_control(frame) else []
        if not events:
            self._dropped(frame)
        for event in events:
            self.on_event(event)

    def _emit(self, kind: str, message: str, attempt: int) -> None:
        if self.reporter is not None:
            self.reporter.emit(Event(kind, message, "", attempt))

    def _report_failure(self, exc: Exception, attempt: int) -> None:
        self._emit("aap_retry", f"The AirPods mic channel failed: {exc}", attempt)
        if attempt == AAP_GIVE_UP:
            self._emit("mic_unavailable", UNAVAILABLE, attempt)

    async def run(self) -> None:
        """Run until cancelled; open or write failures are reported and retried, never raised."""
        await asyncio.sleep(SETTLE_S)
        attempt = 0
        while True:
            try:
                await self._session()
                attempt = 0
            except BaseBumbleError as exc:
                attempt += 1
                self._report_failure(exc, attempt)
            await asyncio.sleep(AAP_RETRY_S)

    async def _session(self) -> None:
        """Open the channel and serve it until it closes."""
        channel = await self._open()
        closed = asyncio.Event()
        if hasattr(channel, "on"):
            channel.on("close", closed.set)
        channel.sink = self._on_sdu
        ca = aap.control(0x28, self.ca_choice) if self.ca_choice is not None else aap.CONVERSATIONAL_AWARENESS_OFF
        sequence = (aap.HANDSHAKE, aap.FEATURES, aap.NOTIFICATIONS, ca, aap.START)
        for index, command in enumerate(sequence):
            if index:
                await asyncio.sleep(COMMAND_GAP_S)
            channel.write(command)
        self._last_start = self._last_frame = self.clock()
        self.started.set()
        while not closed.is_set():
            await asyncio.sleep(POLL_S)
            while not self.outgoing.empty():
                channel.write(self.outgoing.get_nowait())
            now = self.clock()
            if now - self._last_frame >= STALL_S and now - self._last_start >= RESTART_MIN_S:
                channel.write(aap.START)
                self._last_start = now
                self.restarts += 1
                self._check_silent()

    def _check_silent(self) -> None:
        if self.restarts >= MIC_SILENT_RESTARTS and self.frames_seen == 0 and not self._silent_reported:
            self._silent_reported = True
            self._emit("mic_silent", MIC_SILENT, self.restarts)
