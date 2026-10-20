"""Audio capture, playback and endpoint mute behind one backend protocol."""
import atexit
import threading
import time
import warnings
from array import array
from contextlib import contextmanager
from typing import Iterator, Protocol

from podbridge import winusb

CABLE_PLAYBACK = "CABLE Input"
RATE = 48000
CENTER_GAIN = 0.707
LOOPBACK_WAIT_S = 0.5
FL, FR, FC, NONE = (1, 0), (0, 1), (CENTER_GAIN, CENTER_GAIN), (0, 0)
BL, BR = (CENTER_GAIN, 0), (0, CENTER_GAIN)
# Channel order Windows gives an endpoint with this many channels (left gain, right gain each).
LAYOUTS = {
    3: (FL, FR, FC),
    4: (FL, FR, BL, BR),
    5: (FL, FR, FC, BL, BR),
    6: (FL, FR, FC, NONE, BL, BR),
    7: (FL, FR, FC, NONE, FC, BL, BR),
    8: (FL, FR, FC, NONE, BL, BR, BL, BR),
}


class AudioBackend(Protocol):
    def read_loopback(self, frames: int) -> bytes:
        """Block for `frames` 16-bit stereo frames of the default render endpoint."""

    def write_playback(self, pcm: bytes, device: str = CABLE_PLAYBACK) -> None:
        """Play 16-bit PCM to the first output device whose name contains `device`."""

    def get_mute(self) -> bool:
        """Mute state of the default render endpoint."""

    def set_mute(self, muted: bool) -> None:
        """Set the mute state of the default render endpoint."""

    def render_name(self) -> str:
        """Name of the render device whose loopback is captured."""

    def close(self) -> None: ...


@contextmanager
def muted_default_output(backend: AudioBackend, output_source: str | None = None) -> Iterator[None]:
    """Mute the default render endpoint (Decision 15); restore the prior state on exit.

    Does nothing when `output_source` is given. Restores in finally, atexit and on console close.
    """
    if output_source:
        yield
        return
    prior = backend.get_mute()
    restored = False
    lock = threading.Lock()

    def restore() -> None:
        nonlocal restored
        with lock:
            if not restored:
                backend.set_mute(prior)
                restored = True

    atexit.register(restore)
    handler = winusb.install_close_handler(restore)
    try:
        backend.set_mute(True)
        yield
    finally:
        winusb.remove_close_handler(handler)
        restore()
        atexit.unregister(restore)


class FakeBackend:
    """In-memory backend for tests and selftest."""

    def __init__(self, loopback: bytes = b"", muted: bool = False) -> None:
        self._loopback = loopback
        self.muted = muted
        self.played: list[tuple[str, bytes]] = []
        self.closed = False

    def read_loopback(self, frames: int) -> bytes:
        size = frames * 4
        out, self._loopback = self._loopback[:size], self._loopback[size:]
        return out.ljust(size, b"\0")

    def write_playback(self, pcm: bytes, device: str = CABLE_PLAYBACK) -> None:
        self.played.append((device, pcm))

    def get_mute(self) -> bool:
        return self.muted

    def set_mute(self, muted: bool) -> None:
        self.muted = muted

    def render_name(self) -> str:
        return "Default"

    def close(self) -> None:
        self.closed = True


def _clip(value: float) -> int:
    return max(-32768, min(32767, round(value)))


def layout(channels: int) -> tuple[tuple[float, float], ...]:
    """Per-channel (left, right) gains, scaled so a full-scale signal on every channel cannot clip."""
    gains = LAYOUTS.get(channels) or ((1, 0), (0, 1)) + (NONE,) * (channels - 2)
    norm = max(1.0, *(sum(g[side] for g in gains) for side in (0, 1)))
    return tuple((left / norm, right / norm) for left, right in gains)


def downmix(samples: array, channels: int) -> tuple[list[float], list[float]]:
    """Left and right lists from interleaved samples: mono is duplicated, stereo kept, the rest folded."""
    if channels == 1:
        return list(samples), list(samples)
    sides = ([0.0] * (len(samples) // channels) for _ in (0, 1))
    out = []
    for side, acc in enumerate(sides):
        for index, gain in enumerate(layout(channels)):
            if gain[side]:
                acc = [a + gain[side] * b for a, b in zip(acc, samples[index::channels])]
        out.append(acc)
    return out[0], out[1]


def resample(values: list[float], frames: int, prev: float | None = None) -> list[float]:
    """Linear interpolation of a non-empty chunk onto exactly `frames` samples.

    `prev` is the last sample of the previous chunk (None for the first), so the phase carries
    across chunks: output i sits at input position (i + 1) * len / frames - 1.
    """
    step = len(values) / frames
    line = [values[0] if prev is None else prev, *values]
    out = []
    for i in range(frames):
        pos = (i + 1) * step
        low = min(int(pos), len(values))
        high = min(low + 1, len(values))
        out.append(line[low] + (line[high] - line[low]) * (pos - low))
    return out


def to_48k_stereo(raw: bytes, channels: int, rate: int, frames: int, carry: list | None = None) -> bytes:
    """Interleaved 16-bit PCM at any rate and channel count to `frames` 48 kHz 16-bit stereo frames.

    `carry` holds the last left and right sample between calls; an empty read gives silence.
    """
    samples = array("h")
    samples.frombytes(raw[: len(raw) // (2 * channels) * 2 * channels])
    if not samples:
        return bytes(frames * 4)
    carry = carry if carry is not None else [None, None]
    sides = downmix(samples, channels)
    stereo = array("h", bytes(frames * 4))
    for side, values in enumerate(sides):
        stereo[side::2] = array("h", (_clip(v) for v in resample(values, frames, carry[side])))
        carry[side] = values[-1]
    return stereo.tobytes()


# comtypes 1.4.17 warns on import on Python 3.15 (enthought/comtypes#894); fixed upstream for 1.5.0, not yet released
COMTYPES_PY315_WARNING = r"You are running 'comtypes' on Python 3\.15, where the behavior of enum types"


def import_comtypes():
    """comtypes, imported without its Python 3.15 FutureWarning."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=COMTYPES_PY315_WARNING, category=FutureWarning)
        import comtypes
    return comtypes


@contextmanager
def com_initialised() -> Iterator[None]:
    """COM objects are per thread; the console-close handler runs on a thread that never initialised it."""
    comtypes = import_comtypes()
    comtypes.CoInitialize()
    try:
        yield
    finally:
        comtypes.CoUninitialize()


class PyAudioWPatchBackend:
    """Windows backend: WASAPI loopback and VB-CABLE through PyAudioWPatch, mute through pycaw.

    The render endpoint is pinned in `__init__` (Decision 7): `output_source` (a device name part)
    when given, else the default render endpoint. Loopback and mute both use it for the whole run.
    """

    def __init__(self, output_source: str | None = None) -> None:
        import pyaudiowpatch as pyaudio

        self._pa = pyaudio.PyAudio()
        self._wasapi = pyaudio.paWASAPI
        self._in = None
        self._out: dict[str, object] = {}
        self._carry: list = [None, None]
        try:
            self._loopback = self._loopback_device(output_source)
            self._endpoint_id = None if output_source else self._default_endpoint_id()
        except BaseException:
            self._pa.terminate()
            raise

    @staticmethod
    def _default_endpoint_id() -> str:
        # why: pycaw.constants imports comtypes, so com_initialised must import it first, quietly
        with com_initialised():
            from pycaw.constants import EDataFlow, ERole
            from pycaw.pycaw import AudioUtilities

            enumerator = AudioUtilities.GetDeviceEnumerator()
            return enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender.value, ERole.eMultimedia.value).GetId()

    def _loopback_device(self, output_source: str | None) -> dict:
        if output_source:
            name = output_source
        else:
            speakers = self._pa.get_device_info_by_index(
                self._pa.get_host_api_info_by_type(self._wasapi)["defaultOutputDevice"]
            )
            if speakers.get("isLoopbackDevice"):
                return speakers
            name = speakers["name"]
        for dev in self._pa.get_loopback_device_info_generator():
            if name in dev["name"]:
                return dev
        raise RuntimeError(f"no loopback device for {name!r}")

    def read_loopback(self, frames: int) -> bytes:
        dev = self._loopback
        rate, channels = int(dev["defaultSampleRate"]), dev["maxInputChannels"]
        if self._in is None:
            self._in = self._pa.open(
                format=self._pa.get_format_from_width(2),
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=dev["index"],
            )
        need = round(frames * rate / RATE)
        deadline = time.monotonic() + LOOPBACK_WAIT_S
        while self._in.get_read_available() < need:
            if time.monotonic() >= deadline:
                return bytes(frames * 4)
            time.sleep(0.01)
        raw = self._in.read(need)
        return to_48k_stereo(raw, channels, rate, frames, self._carry)

    def render_name(self) -> str:
        return self._loopback["name"].removesuffix(" [Loopback]")

    def write_playback(self, pcm: bytes, device: str = CABLE_PLAYBACK) -> None:
        stream = self._out.get(device)
        if stream is None:
            index = next(
                (
                    d["index"]
                    for d in map(self._pa.get_device_info_by_index, range(self._pa.get_device_count()))
                    if device in d["name"] and d["maxOutputChannels"] > 0
                ),
                None,
            )
            if index is None:
                raise RuntimeError(f"output device {device!r} not found")
            stream = self._out[device] = self._pa.open(
                format=self._pa.get_format_from_width(2),
                channels=2,
                rate=48000,
                output=True,
                output_device_index=index,
            )
        stream.write(pcm)

    def _volume(self):
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        device = AudioUtilities.GetDeviceEnumerator().GetDevice(self._endpoint_id)
        return device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)

    def get_mute(self) -> bool:
        with com_initialised():
            return bool(self._volume().GetMute())

    def set_mute(self, muted: bool) -> None:
        with com_initialised():
            self._volume().SetMute(int(muted), None)

    def close(self) -> None:
        for stream in [self._in, *self._out.values()]:
            if stream is not None:
                stream.close()
        self._pa.terminate()
