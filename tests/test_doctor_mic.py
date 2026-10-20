import asyncio
import struct

import pytest

from podbridge import aap, aap_session, doctor


def mic_sdu(*units: bytes) -> bytes:
    body = b"".join(struct.pack("<IB", 0, len(u)) + u for u in units)
    return bytes.fromhex("04000400580001000000000000000000000000000000") + body


def slow_start(monkeypatch):
    """Settle + open + gaps (0.2 + 0.05 + 0.4 s) outlast the mic window (0.3 s), as 2.7 s+ outlasts 3.0 s on hardware."""
    monkeypatch.setattr(aap_session, "SETTLE_S", 0.2)
    monkeypatch.setattr(aap_session, "COMMAND_GAP_S", 0.1)
    monkeypatch.setattr(aap_session, "POLL_S", 0.02)
    monkeypatch.setattr(doctor, "MIC_WINDOW_S", 0.3)
    monkeypatch.setattr(doctor, "OPEN_WAIT_S", 1.0, raising=False)


def run_stage(connection):
    ctx = doctor.Context()
    ctx.connection = connection
    return asyncio.run(doctor.mic_frames(ctx))


def test_mic_frames_that_follow_a_late_start_are_counted(monkeypatch):
    slow_start(monkeypatch)

    class Channel:
        sink = None

        def write(self, data):
            if data == aap.START:
                asyncio.get_running_loop().call_later(0.1, self.sink, mic_sdu(b"abc"))

    class Connection:
        async def create_l2cap_channel(self, spec):
            await asyncio.sleep(0.05)
            return Channel()

    assert run_stage(Connection()).endswith("frames/s")


def test_a_channel_that_never_opens_names_the_aap_channel(monkeypatch):
    slow_start(monkeypatch)
    monkeypatch.setattr(doctor, "OPEN_WAIT_S", 0.1, raising=False)

    class Connection:
        async def create_l2cap_channel(self, spec):
            await asyncio.Event().wait()

    with pytest.raises(doctor.StageFailed) as failure:
        run_stage(Connection())
    assert "AAP channel did not open" in str(failure.value)
    assert "reseat" not in str(failure.value)
