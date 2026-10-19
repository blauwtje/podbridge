"""`podbridge selftest`: run both pipelines on in-memory fakes."""
import asyncio
import math
import struct

from podbridge import aap
from podbridge.a2dp_encoder import A2dpEncoder
from podbridge.app import mic_pipeline, output_pipeline
from podbridge.audio_io import CABLE_PLAYBACK, FakeBackend
from podbridge.host import EncodedPacketSource
from podbridge.mic_decoder import MicDecoder

TONE_HZ = 440
TONE_SECONDS = 1
RATE = 48000
SDU_UNITS = 5
UNIT_SIZE = 60
TIMEOUT_S = 10.0


def tone() -> bytes:
    values = (int(12000 * math.sin(2 * math.pi * TONE_HZ * i / RATE)) for i in range(RATE * TONE_SECONDS))
    return b"".join(struct.pack("<hh", v, v) for v in values)


def mic_sdu() -> bytes:
    """A mic SDU of fake access units: header, then 5-byte record heads whose last byte is the size."""
    sdu = bytearray(aap.START[:4] + bytes.fromhex("5800") + bytes.fromhex("0100")).ljust(aap.HEADER_LEN, b"\0")
    for index in range(SDU_UNITS):
        sdu += bytes(4) + bytes([UNIT_SIZE]) + bytes([index + 1]) * UNIT_SIZE
    return bytes(sdu)


async def wait_until(condition) -> bool:
    try:
        async with asyncio.timeout(TIMEOUT_S):
            while not condition():
                await asyncio.sleep(0.01)
    except TimeoutError:
        return False
    return True


async def check_output() -> bool:
    source = EncodedPacketSource(A2dpEncoder("aac"))
    task = asyncio.create_task(output_pipeline(FakeBackend(loopback=tone()), source))
    try:
        return await wait_until(lambda: not source._queue.empty())
    finally:
        task.cancel()


async def check_mic() -> bool:
    backend = FakeBackend()
    queue: asyncio.Queue = asyncio.Queue()
    for unit in aap.parse_mic_sdu(mic_sdu()):
        queue.put_nowait(unit)
    task = asyncio.create_task(mic_pipeline(queue, MicDecoder(), backend))
    try:
        return await wait_until(lambda: any(device == CABLE_PLAYBACK for device, _ in backend.played))
    finally:
        task.cancel()


async def selftest() -> dict[str, bool]:
    return {"output pipeline": await check_output(), "mic pipeline": await check_mic()}


def main(args=None) -> int:
    results = asyncio.run(selftest())
    for name, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    return 0 if all(results.values()) else 1
