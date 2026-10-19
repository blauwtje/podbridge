"""Audio capture, playback and endpoint mute behind one backend protocol."""
import atexit
from contextlib import contextmanager
from typing import Iterator, Protocol

from podbridge import winusb

CABLE_PLAYBACK = "CABLE Input"


class AudioBackend(Protocol):
    def read_loopback(self, frames: int) -> bytes:
        """Block for `frames` 16-bit stereo frames of the default render endpoint."""

    def write_playback(self, pcm: bytes, device: str = CABLE_PLAYBACK) -> None:
        """Play 16-bit PCM to the first output device whose name contains `device`."""

    def get_mute(self) -> bool:
        """Mute state of the default render endpoint."""

    def set_mute(self, muted: bool) -> None:
        """Set the mute state of the default render endpoint."""

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

    def restore() -> None:
        nonlocal restored
        if not restored:
            restored = True
            backend.set_mute(prior)

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

    def close(self) -> None:
        self.closed = True


class PyAudioWPatchBackend:
    """Windows backend: WASAPI loopback and VB-CABLE through PyAudioWPatch, mute through pycaw."""

    def __init__(self) -> None:
        import pyaudiowpatch as pyaudio

        self._pa = pyaudio.PyAudio()
        self._in = None
        self._out: dict[str, object] = {}

    def _loopback_device(self) -> dict:
        speakers = self._pa.get_device_info_by_index(
            self._pa.get_host_api_info_by_type(self._pa.paWASAPI)["defaultOutputDevice"]
        )
        if speakers.get("isLoopbackDevice"):
            return speakers
        for dev in self._pa.get_loopback_device_info_generator():
            if speakers["name"] in dev["name"]:
                return dev
        raise RuntimeError("no loopback device for the default output")

    def read_loopback(self, frames: int) -> bytes:
        if self._in is None:
            dev = self._loopback_device()
            self._in = self._pa.open(
                format=self._pa.get_format_from_width(2),
                channels=dev["maxInputChannels"],
                rate=int(dev["defaultSampleRate"]),
                input=True,
                input_device_index=dev["index"],
            )
        return self._in.read(frames)

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

        speakers = AudioUtilities.GetSpeakers()
        return speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(
            IAudioEndpointVolume
        )

    def get_mute(self) -> bool:
        return bool(self._volume().GetMute())

    def set_mute(self, muted: bool) -> None:
        self._volume().SetMute(int(muted), None)

    def close(self) -> None:
        for stream in [self._in, *self._out.values()]:
            if stream is not None:
                stream.close()
        self._pa.terminate()
