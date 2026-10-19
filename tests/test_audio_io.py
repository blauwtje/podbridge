import array
import threading

import pytest

from podbridge.audio_io import CABLE_PLAYBACK, FakeBackend, muted_default_output, to_48k_stereo


def test_loopback_pads_and_consumes():
    b = FakeBackend(loopback=b"\x01" * 4)
    assert b.read_loopback(2) == b"\x01" * 4 + b"\0" * 4
    assert b.read_loopback(1) == b"\0" * 4


def test_playback_defaults_to_cable():
    b = FakeBackend()
    b.write_playback(b"abcd")
    assert b.played == [(CABLE_PLAYBACK, b"abcd")]


@pytest.mark.parametrize("prior", [False, True])
def test_mute_restores_prior_state(prior):
    b = FakeBackend(muted=prior)
    with muted_default_output(b):
        assert b.muted is True
    assert b.muted is prior


def test_mute_restores_on_error():
    b = FakeBackend(muted=False)
    with pytest.raises(RuntimeError):
        with muted_default_output(b):
            raise RuntimeError
    assert b.muted is False


def test_output_source_skips_mute():
    b = FakeBackend(muted=False)
    with muted_default_output(b, "Speakers"):
        assert b.muted is False


def test_mute_restores_when_console_closes(monkeypatch):
    from podbridge import winusb

    handlers = []
    monkeypatch.setattr(winusb, "install_close_handler", lambda restore: handlers.append(restore) or object())
    monkeypatch.setattr(winusb, "remove_close_handler", lambda handler: None)
    b = FakeBackend(muted=False)
    with muted_default_output(b):
        assert b.muted is True
        assert handlers, "no console-close handler registered"
        handlers[0]()
        assert b.muted is False


class FakeStream:
    def __init__(self, **kw):
        self.kw = kw
        self.requested = []

    def read(self, n):
        self.requested.append(n)
        return b"\x00\x10" * (n * self.kw["channels"])

    def close(self):
        pass


class FakePyAudio:
    default_name = "Speakers"

    def __init__(self):
        self.opened = []
        self.terminated = False
        self.devices = [
            {"index": 0, "name": "Speakers", "isLoopbackDevice": False, "maxInputChannels": 0},
            {"index": 1, "name": "Headset", "isLoopbackDevice": False, "maxInputChannels": 0},
            {"index": 2, "name": "Speakers [Loopback]", "isLoopbackDevice": True, "maxInputChannels": 6,
             "defaultSampleRate": 44100.0},
            {"index": 3, "name": "Headset [Loopback]", "isLoopbackDevice": True, "maxInputChannels": 2,
             "defaultSampleRate": 48000.0},
        ]

    def get_host_api_info_by_type(self, kind):
        assert kind == 13
        return {"defaultOutputDevice": 0 if self.default_name == "Speakers" else 1}

    def get_device_info_by_index(self, i):
        return self.devices[i]

    def get_loopback_device_info_generator(self):
        return (d for d in self.devices if d["isLoopbackDevice"])

    def get_format_from_width(self, width):
        return width

    def open(self, **kw):
        self.opened.append(kw)
        return FakeStream(**kw)

    def terminate(self):
        self.terminated = True


class FakeVolume:
    def __init__(self, log, endpoint_id):
        self.log, self.id = log, endpoint_id

    def Activate(self, *a):
        return self

    def QueryInterface(self, *a):
        return self

    def GetMute(self):
        self.log.append(("get", self.id))
        return 0

    def SetMute(self, value, ctx):
        self.log.append(("set", self.id, value))


@pytest.fixture
def windows(monkeypatch):
    import sys
    import threading
    import types

    pa = FakePyAudio()
    log = []
    default = {"id": "{speakers}"}
    com = {}

    class Device(FakeVolume):
        def GetId(self):
            return self.id

    class Enumerator:
        def GetDevice(self, endpoint_id):
            return Device(log, endpoint_id)

        def GetDefaultAudioEndpoint(self, flow, role):
            assert (flow, role) == (0, 1), "default render endpoint, multimedia role"
            return Device(log, default["id"])

    class Wrapper:
        """pycaw's AudioDevice: an .id attribute and no GetId()."""

        def __init__(self, endpoint_id):
            self.id = endpoint_id

    class Utilities:
        @staticmethod
        def GetSpeakers():
            return Wrapper(default["id"])

        @staticmethod
        def GetDeviceEnumerator():
            return Enumerator()

        @staticmethod
        def GetAllDevices():
            return [types.SimpleNamespace(id="{speakers}", FriendlyName="Speakers"),
                    types.SimpleNamespace(id="{headset}", FriendlyName="Headset")]

    def co_init():
        com[threading.get_ident()] = com.get(threading.get_ident(), 0) + 1

    def need_com(name):
        def inner(*a):
            assert com.get(threading.get_ident()), "COM not initialised on this thread"
        return inner

    pycaw = types.ModuleType("pycaw.pycaw")
    pycaw.AudioUtilities = Utilities
    pycaw.IAudioEndpointVolume = types.SimpleNamespace(_iid_=None)
    comtypes = types.ModuleType("comtypes")
    comtypes.CLSCTX_ALL = 0
    comtypes.CoInitialize = co_init
    comtypes.CoUninitialize = lambda: com.__setitem__(threading.get_ident(), com[threading.get_ident()] - 1)
    constants = types.ModuleType("pycaw.constants")
    constants.EDataFlow = types.SimpleNamespace(eRender=types.SimpleNamespace(value=0))
    constants.ERole = types.SimpleNamespace(eMultimedia=types.SimpleNamespace(value=1))
    monkeypatch.setitem(sys.modules, "pycaw.constants", constants)
    mod = types.ModuleType("pyaudiowpatch")
    mod.PyAudio = lambda: pa
    mod.paWASAPI = 13  # a module constant in pyaudiowpatch, not a PyAudio attribute
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", mod)
    monkeypatch.setitem(sys.modules, "comtypes", comtypes)
    monkeypatch.setitem(sys.modules, "pycaw", types.ModuleType("pycaw"))
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", pycaw)
    orig = Device.GetMute
    Device.GetMute = lambda self: need_com("get")() or orig(self)
    orig_set = Device.SetMute
    Device.SetMute = lambda self, v, c: need_com("set")() or orig_set(self, v, c)
    return types.SimpleNamespace(pa=pa, log=log, default=default)


def test_loopback_is_48k_stereo_whatever_the_endpoint(windows):
    from podbridge.audio_io import PyAudioWPatchBackend

    backend = PyAudioWPatchBackend()
    out = backend.read_loopback(960)
    assert len(out) == 960 * 4
    assert windows.pa.opened[0]["channels"] == 6 and windows.pa.opened[0]["rate"] == 44100
    assert windows.pa.opened[0]["input_device_index"] == 2
    assert backend._in.requested == [882]


def test_resample_downmixes_and_keeps_pitch():
    import array
    import math

    from podbridge.audio_io import to_48k_stereo

    mono = array.array("h", (int(10000 * math.sin(2 * math.pi * 441 * i / 44100)) for i in range(441)))
    out = array.array("h")
    out.frombytes(to_48k_stereo(mono.tobytes(), 1, 44100, 480))
    assert len(out) == 960 and out[0::2] == out[1::2]
    crossings = sum(1 for a, b in zip(out[0::2], out[2::2]) if a < 0 <= b)
    assert crossings in (4, 5)


def test_output_source_changes_capture_device(windows):
    from podbridge.audio_io import PyAudioWPatchBackend

    PyAudioWPatchBackend(output_source="Headset").read_loopback(480)
    assert windows.pa.opened[0]["input_device_index"] == 3


def test_endpoint_is_pinned_at_start(windows):
    from podbridge.audio_io import PyAudioWPatchBackend

    backend = PyAudioWPatchBackend()
    windows.default["id"] = "{headset}"
    windows.pa.default_name = "Headset"
    backend.set_mute(True)
    backend.read_loopback(480)
    assert windows.log == [("set", "{speakers}", 1)]
    assert windows.pa.opened[0]["input_device_index"] == 2


def test_mute_restore_initialises_com_on_its_own_thread(windows):
    import threading

    from podbridge.audio_io import PyAudioWPatchBackend

    backend = PyAudioWPatchBackend()
    errors = []

    def close_handler_thread():
        try:
            backend.set_mute(False)
        except AssertionError as exc:
            errors.append(exc)

    t = threading.Thread(target=close_handler_thread)
    t.start()
    t.join()
    assert not errors and windows.log == [("set", "{speakers}", 0)]


def pcm(values):
    return array.array("h", values).tobytes()


def decode(raw):
    out = array.array("h")
    out.frombytes(raw)
    return list(out[0::2]), list(out[1::2])


def test_restore_is_once_only_and_waits_for_the_running_restore(monkeypatch):
    from podbridge import winusb

    handlers = []
    monkeypatch.setattr(winusb, "install_close_handler", lambda restore: handlers.append(restore) or object())
    monkeypatch.setattr(winusb, "remove_close_handler", lambda handler: None)
    entered, release, calls = threading.Event(), threading.Event(), []

    class Slow(FakeBackend):
        def set_mute(self, muted):
            calls.append(muted)
            if not muted:
                entered.set()
                release.wait(5)
            super().set_mute(muted)

    b = Slow(muted=False)
    with muted_default_output(b):
        first = threading.Thread(target=handlers[0])
        second = threading.Thread(target=handlers[0])
        first.start()
        assert entered.wait(5)
        second.start()
        second.join(0.2)
        assert second.is_alive(), "second restore returned before the first finished"
        release.set()
        first.join(5)
        second.join(5)
    assert calls == [True, False] and b.muted is False


def test_restore_retries_after_a_failed_set_mute(monkeypatch):
    from podbridge import winusb

    handlers = []
    monkeypatch.setattr(winusb, "install_close_handler", lambda restore: handlers.append(restore) or object())
    monkeypatch.setattr(winusb, "remove_close_handler", lambda handler: None)

    class Flaky(FakeBackend):
        failures = 1

        def set_mute(self, muted):
            if not muted and self.failures:
                self.failures -= 1
                raise OSError("endpoint gone")
            super().set_mute(muted)

    b = Flaky(muted=False)
    with muted_default_output(b):
        with pytest.raises(OSError):
            handlers[0]()
        assert b.muted is True
        handlers[0]()
    assert b.muted is False


def test_mono_is_duplicated_and_stereo_kept():
    assert decode(to_48k_stereo(pcm([100, -200, 300]), 1, 48000, 3)) == ([100, -200, 300],) * 2
    assert decode(to_48k_stereo(pcm([1, 2, 3, 4]), 2, 48000, 2)) == ([1, 3], [2, 4])


def test_quad_rear_channels_go_to_their_own_side():
    left, right = decode(to_48k_stereo(pcm([0, 0, 10000, 0]), 4, 48000, 1))
    assert left[0] > 0 and right[0] == 0
    left, right = decode(to_48k_stereo(pcm([0, 0, 0, 10000]), 4, 48000, 1))
    assert left[0] == 0 and right[0] > 0


@pytest.mark.parametrize("channels", [3, 4, 5, 6, 7, 8, 10])
def test_loud_content_on_every_channel_is_scaled_not_clipped(channels):
    raw = pcm([20000] * channels * 4)
    left, right = decode(to_48k_stereo(raw, channels, 48000, 4))
    assert 0 < max(left + right) <= 20001


def test_front_channels_survive_the_fold_at_a_sensible_level():
    left, right = decode(to_48k_stereo(pcm([20000, -20000, 0, 0, 0, 0]), 6, 48000, 1))
    assert 5000 < left[0] < 20000 and -20000 < right[0] < -5000


def test_output_is_clipped_to_16_bit():
    left, _ = decode(to_48k_stereo(pcm([32767, 32767, -32768, -32768]), 2, 44100, 4))
    assert max(left) <= 32767 and min(left) >= -32768


def test_chunks_resample_like_one_long_read():
    ramp = [i * 10 for i in range(1764)]
    whole = decode(to_48k_stereo(pcm(ramp), 1, 44100, 1920))[0]
    carry = [None, None]
    parts = [decode(to_48k_stereo(pcm(ramp[i : i + 882]), 1, 44100, 960, carry))[0] for i in (0, 882)]
    assert all(abs(a - b) <= 1 for a, b in zip(parts[0] + parts[1], whole))
    assert abs(parts[1][0] - parts[0][-1]) <= 12, "step across the chunk edge"


def test_empty_read_gives_silence(windows):
    from podbridge.audio_io import PyAudioWPatchBackend

    backend = PyAudioWPatchBackend()
    backend.read_loopback(960)
    backend._in.read = lambda n: b""
    assert backend.read_loopback(960) == bytes(960 * 4)


def test_failed_construction_terminates_pyaudio(windows):
    from podbridge.audio_io import PyAudioWPatchBackend

    with pytest.raises(RuntimeError, match="no loopback device"):
        PyAudioWPatchBackend(output_source="Missing")
    assert windows.pa.terminated


def test_default_endpoint_id_from_the_enumerator(windows):
    from podbridge.audio_io import PyAudioWPatchBackend

    assert PyAudioWPatchBackend()._endpoint_id == "{speakers}"


def test_render_name_is_the_captured_device(windows):
    from podbridge.audio_io import PyAudioWPatchBackend

    assert PyAudioWPatchBackend(output_source="Headset").render_name() == "Headset"
    assert PyAudioWPatchBackend().render_name() == "Speakers"
