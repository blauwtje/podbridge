"""Audio capture, playback and endpoint mute behind one backend protocol."""
import atexit
import json
import logging
import os
import threading
import time
import warnings
from array import array
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Protocol

from podbridge import winusb

log = logging.getLogger("podbridge")
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

    def get_volume(self) -> float:
        """Linear gain 0.0..1.0 the default render endpoint applies, as the speakers would play it."""

    def watch_volume(self, wake: Callable[[], None]) -> Callable[[], None]:
        """Call `wake` (without blocking) when the endpoint volume or mute changes; returns the unregister."""

    def render_name(self) -> str:
        """Name of the render device whose loopback is captured."""

    def close(self) -> None: ...


MUTE_STATE_FILE = "mute-state.json"


def mute_state_path(machine) -> Path | None:
    """mute-state.json next to the driver state, or None for a machine without one."""
    state_path = getattr(machine, "state_path", None)
    return state_path.with_name(MUTE_STATE_FILE) if state_path is not None else None


def saved_mute(path: Path | None) -> bool | None:
    """The mute state a killed run saved before it muted, or None when there is none."""
    try:
        muted = json.loads(path.read_text(encoding="utf-8"))["muted"] if path else None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return muted if isinstance(muted, bool) else None


def save_mute(path: Path | None, muted: bool) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"muted": muted}), encoding="utf-8")
    os.replace(tmp, path)


class Volume:
    """The endpoint gain the output pipeline applies; written by the watcher, read per chunk."""

    def __init__(self, gain: float = 1.0) -> None:
        self.gain = gain


def apply_gain(pcm: bytes, gain: float, previous: float | None = None) -> bytes:
    """16-bit stereo PCM scaled by `gain` and clipped to int16; gain 1.0 returns the bytes as they are.

    With a `previous` gain that differs, the factor moves linearly from it to `gain` across the chunk
    (one factor per frame, both channels alike), so a change never makes a step.
    """
    if previous is None or previous == gain:
        previous = gain
        if gain == 1.0:
            return pcm
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if previous == gain:
        return array("h", (_clip(v * gain) for v in samples)).tobytes()
    frames = (len(samples) + 1) // 2
    step = (gain - previous) / frames
    return array("h", (_clip(v * (previous + step * (i // 2 + 1))) for i, v in enumerate(samples))).tobytes()


VOLUME_POLL_S = 0.1


@contextmanager
def muted_default_output(backend: AudioBackend, output_source: str | None = None,
                         state_path: Path | None = None) -> Iterator[Volume]:
    """Mute the default render endpoint (Decision 15); restore the prior state on exit.

    Yields the endpoint gain. Windows taps the loopback before volume and mute, so the capture ignores the
    slider; a watcher keeps the endpoint muted (moving the slider unmutes it) and tracks its volume.
    Does nothing, and yields a fixed gain of 1.0, when `output_source` is given.
    Restores in finally, atexit and on console close.
    The prior state is also saved to `state_path`, so a run after a hard kill restores it, not the kill's mute.
    """
    volume = Volume()
    if output_source:
        yield volume
        return
    saved = saved_mute(state_path)
    prior = saved if saved is not None else backend.get_mute()
    log.info("mute: endpoint %r, saved-file value %s, prior value %s", backend.render_name(), saved, prior)
    if saved is None:
        save_mute(state_path, prior)
    restored = False
    lock = threading.Lock()
    stop = threading.Event()
    wake = threading.Event()
    unregister: Callable[[], None] | None = None

    def release() -> None:
        nonlocal unregister
        with lock:
            gone, unregister = unregister, None
        if gone is not None:
            try:
                gone()
            except Exception:
                log.warning("volume notification unregister failed", exc_info=True)

    def restore() -> None:
        nonlocal restored
        release()  # why: no notification may reach the watcher once the prior state is being put back
        with lock:
            stop.set()  # why: the watcher must not mute again after the prior state is back
            wake.set()
            if not restored:
                backend.set_mute(prior)
                log.info("mute: restored to %s", prior)
                restored = True
                if state_path is not None:
                    state_path.unlink(missing_ok=True)

    def watch() -> None:
        reported: set[type] = set()
        while True:
            wake.clear()  # why: before the reads, so a change during them runs the loop again
            try:
                gain, muted = backend.get_volume(), backend.get_mute()
                if gain != volume.gain:
                    volume.gain = gain
                    log.info("volume: gain %.3f", gain)
                if muted is False:
                    with lock:
                        if not stop.is_set():
                            backend.set_mute(True)
                            log.info("mute: endpoint unmuted (volume %.2f); muted again", gain)
            except Exception as exc:
                if type(exc) not in reported:
                    reported.add(type(exc))
                    log.warning("volume watcher failed", exc_info=True)
            wake.wait(VOLUME_POLL_S)
            if stop.is_set():
                return

    atexit.register(restore)
    handler = winusb.install_close_handler(restore)
    watcher = threading.Thread(target=watch, name="volume-watcher", daemon=True)
    try:
        backend.set_mute(True)
        log.info("mute: set_mute(True) ran")
        try:
            registered = backend.watch_volume(wake.set)
            with lock:
                unregister = registered
        except Exception:
            log.warning("volume change notifications unavailable; polling every %.2f s", VOLUME_POLL_S, exc_info=True)
        watcher.start()
        yield volume
    finally:
        winusb.remove_close_handler(handler)
        release()
        stop.set()
        wake.set()
        if watcher.is_alive():
            watcher.join()
        restore()
        atexit.unregister(restore)


class FakeBackend:
    """In-memory backend for tests and selftest."""

    def __init__(self, loopback: bytes = b"", muted: bool = False) -> None:
        self._loopback = loopback
        self.muted = muted
        self.volume = 1.0
        self.wakers: list[Callable[[], None]] = []
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

    def get_volume(self) -> float:
        return self.volume

    def watch_volume(self, wake: Callable[[], None]) -> Callable[[], None]:
        self.wakers.append(wake)
        return lambda: self.wakers.remove(wake)

    @property
    def watching(self) -> int:
        return len(self.wakers)

    def fire_volume_change(self) -> None:
        for wake in list(self.wakers):
            wake()

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
        self.silent_timeouts = 0
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
            log.info("loopback input opens: %r, %d Hz, %d channels", dev["name"], rate, channels)
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
                self.silent_timeouts += 1
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

    def get_volume(self) -> float:
        with com_initialised():
            # why: the loopback is tapped before the endpoint volume, so podbridge applies the level Windows would; the
            # AirPods get Windows' Bluetooth behaviour (AVRCP absolute volume, linear in the slider), not the speakers'
            # dB curve, which leaves 30% near -50 dBFS
            return float(self._volume().GetMasterVolumeLevelScalar())

    def watch_volume(self, wake: Callable[[], None]) -> Callable[[], None]:
        """Register an IAudioEndpointVolumeCallback whose OnNotify only calls `wake`.

        The callback lives in its own thread, in an MTA: a callback object in an STA needs a message pump to be
        called. That thread registers, waits, then unregisters and uninitialises COM; the returned function joins it.
        """
        ready, done = threading.Event(), threading.Event()
        failure: list[BaseException] = []

        def hold() -> None:
            comtypes = None
            endpoint = callback = None
            try:
                comtypes = import_comtypes()
                comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
                from pycaw.callbacks import AudioEndpointVolumeCallback

                class Notify(AudioEndpointVolumeCallback):
                    def OnNotify(self, pNotify):  # why: runs on a COM thread; only signal, never call back into COM
                        try:
                            wake()
                        except Exception:
                            pass

                endpoint, callback = self._volume(), Notify()
                endpoint.RegisterControlChangeNotify(callback)
            except BaseException as exc:
                failure.append(exc)
                endpoint = None
            finally:
                ready.set()
            try:
                if endpoint is not None:
                    done.wait()
                    endpoint.UnregisterControlChangeNotify(callback)
            except Exception:
                log.warning("volume notification unregister failed", exc_info=True)
            finally:
                endpoint = callback = None
                if comtypes is not None:
                    comtypes.CoUninitialize()

        thread = threading.Thread(target=hold, name="volume-callback", daemon=True)
        thread.start()
        ready.wait()
        if failure:
            thread.join()
            raise failure[0]

        def unregister() -> None:
            done.set()
            thread.join()

        return unregister

    def close(self) -> None:
        for stream in [self._in, *self._out.values()]:
            if stream is not None:
                stream.close()
        self._pa.terminate()
