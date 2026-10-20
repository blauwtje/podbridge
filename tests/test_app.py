import asyncio
import hashlib
import json
import types

import pytest

from podbridge import __main__ as cli, app, paths, selftest
from podbridge.audio_io import CABLE_PLAYBACK, FakeBackend
from podbridge.host import EncodedPacketSource
from podbridge.a2dp_encoder import A2dpEncoder


class StubDecoder:
    def decode(self, au):
        from array import array

        return array("h", [1, -2, 3])


def test_selftest_passes_both_pipelines(capsys):
    assert selftest.main() == 0
    out = capsys.readouterr().out
    assert "PASS output pipeline" in out and "PASS mic pipeline" in out


def test_output_pipeline_feeds_encoded_frames():
    async def go():
        source = EncodedPacketSource(A2dpEncoder("aac"))
        task = asyncio.create_task(app.output_pipeline(FakeBackend(loopback=selftest.tone()), lambda: source))
        assert await selftest.wait_until(lambda: not source._queue.empty())
        task.cancel()

    asyncio.run(go())


def test_mic_pipeline_writes_mono_as_stereo_to_cable():
    async def go():
        backend, queue = FakeBackend(), asyncio.Queue()
        queue.put_nowait(b"au")
        task = asyncio.create_task(app.mic_pipeline(queue, StubDecoder(), backend))
        assert await selftest.wait_until(lambda: backend.played)
        task.cancel()
        return backend.played[0]

    device, pcm = asyncio.run(go())
    assert device == CABLE_PLAYBACK
    assert pcm == b"".join(v.to_bytes(2, "little", signed=True) * 2 for v in (1, -2, 3))


def test_restore_accepts_remove_cert_flag():
    assert cli.build_parser().parse_args(["restore", "--remove-cert"]).remove_cert is True
    assert cli.build_parser().parse_args(["restore"]).remove_cert is False


def test_device_file_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert app.saved_address() is None
    app.save_address("AA:BB:CC:DD:EE:FF")
    assert app.saved_address() == "AA:BB:CC:DD:EE:FF"
    assert json.loads((tmp_path / "podbridge" / "device.json").read_text()) == {"address": "AA:BB:CC:DD:EE:FF"}


def test_parse_transport():
    assert app.parse_transport("usb:0bda:887b") == (0x0BDA, 0x887B)
    with pytest.raises(ValueError):
        app.parse_transport("serial:1:2")


FAKE_FIRMWARE = b"Realtech" + b"x" * 64  # a fake EPATCH v1 file; its sha256 stands in for the pinned one


def test_firmware_downloads_to_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(app, "FIRMWARE_SHA256", hashlib.sha256(FAKE_FIRMWARE).hexdigest())
    urls = []
    monkeypatch.setattr(app, "download", lambda url: urls.append(url) or FAKE_FIRMWARE)
    assert app.firmware(None) == 0
    assert [u.rsplit("/", 1)[1].split("?")[0] for u in urls] == [app.FIRMWARE_FILE, app.FIRMWARE_CONFIG]
    assert paths.firmware_dir() == tmp_path / "podbridge" / "firmware"
    assert (paths.firmware_dir() / app.FIRMWARE_FILE).read_bytes() == FAKE_FIRMWARE


def test_firmware_config_is_optional_but_firmware_is_not(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(app, "FIRMWARE_SHA256", hashlib.sha256(FAKE_FIRMWARE).hexdigest())

    def only_firmware(url):
        if url.endswith(app.FIRMWARE_CONFIG):
            raise OSError("404")
        return FAKE_FIRMWARE

    monkeypatch.setattr(app, "download", only_firmware)
    assert app.firmware(None) == 0

    def nothing(url):
        raise OSError("offline")

    monkeypatch.setattr(app, "download", nothing)
    assert app.firmware(None) == 1


def test_output_pipeline_follows_source_swap():
    async def go():
        host = types.SimpleNamespace(source=EncodedPacketSource(A2dpEncoder("aac")))
        old = host.source
        task = asyncio.create_task(app.output_pipeline(FakeBackend(loopback=selftest.tone()), lambda: host.source))
        await asyncio.sleep(0)
        host.source = new = EncodedPacketSource(A2dpEncoder("sbc"))
        assert await selftest.wait_until(lambda: not new._queue.empty())
        task.cancel()

    asyncio.run(go())


def test_run_passes_output_source_to_backend(preflight_usb_only, monkeypatch):
    from contextlib import contextmanager

    from podbridge import audio_io, winusb

    seen = []

    class Spy(FakeBackend):
        def __init__(self, output_source=None):
            super().__init__()
            seen.append(output_source)

    @contextmanager
    def fake_bound(vid, pid, machine=None):
        yield machine

    async def session(host, backend, *rest):
        pass

    monkeypatch.setattr(winusb, "bound", fake_bound)
    monkeypatch.setattr(audio_io, "PyAudioWPatchBackend", Spy)
    monkeypatch.setattr(app, "run_session", session)
    args = types.SimpleNamespace(transport="usb:0bda:887b", output_source="Headset", address="AA")
    app.run(args, machine=object())
    assert seen == ["Headset"]


def test_start_stream_retries_with_sbc():
    class FakeHost:
        codec = "aac"
        source = None

        async def stream(self, connection):
            if self.codec == "aac":
                raise RuntimeError("rejected")
            return "stream"

    host = FakeHost()
    assert asyncio.run(app.start_stream(host, None)) == "stream"
    assert host.codec == "sbc" and host.source.encoder.codec == "sbc"


def test_run_restores_driver_around_session(preflight_usb_only, monkeypatch, capsys):
    from contextlib import contextmanager

    from podbridge import audio_io, winusb

    events = []

    @contextmanager
    def fake_bound(vid, pid, machine=None):
        events.append(("bind", vid, pid))
        try:
            yield machine
        finally:
            events.append("restore")

    async def failing_session(host, backend, *rest):
        events.append("session")
        raise RuntimeError("boom")

    monkeypatch.setattr(winusb, "bound", fake_bound)
    monkeypatch.setattr(audio_io, "PyAudioWPatchBackend", lambda output_source=None: FakeBackend())
    monkeypatch.setattr(app, "run_session", failing_session)
    args = types.SimpleNamespace(transport="usb:0bda:887b", output_source="x", address="AA:BB:CC:DD:EE:FF")
    assert app.run(args, machine=object()) == 1
    assert events == [("bind", 0x0BDA, 0x887B), "session", "restore"]
    assert "Unexpected error: boom" in capsys.readouterr().err


def test_run_reports_a_backend_that_cannot_open(preflight_usb_only, monkeypatch, capsys):
    from podbridge import audio_io

    def broken(output_source=None):
        raise RuntimeError("no loopback device for 'x'")

    monkeypatch.setattr(audio_io, "PyAudioWPatchBackend", broken)
    args = types.SimpleNamespace(transport="usb:0bda:887b", output_source="x", address="AA")
    assert app.run(args, machine=object()) == 1
    assert "no loopback device" in capsys.readouterr().err


@pytest.fixture
def preflight_usb_only(monkeypatch, tmp_path):
    from podbridge import doctor

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(doctor, "PREFLIGHT", (doctor.usb_id,))


def test_run_stops_before_binding_when_preflight_fails(monkeypatch, tmp_path, capsys):
    from podbridge import doctor, winusb

    async def failing(ctx):
        raise doctor.StageFailed("fix this")

    failing.__doc__ = "setup"
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(doctor, "PREFLIGHT", (failing,))
    monkeypatch.setattr(winusb, "bound", lambda *a, **k: pytest.fail("bound the driver"))
    args = types.SimpleNamespace(transport="usb:0bda:887b", output_source="x")
    assert app.run(args, machine=object()) == 1
    assert "FAIL setup: fix this" in capsys.readouterr().out


def test_run_prints_nothing_when_the_preflight_passes(preflight_usb_only, monkeypatch, capsys):
    from contextlib import contextmanager

    from podbridge import audio_io, winusb

    @contextmanager
    def fake_bound(vid, pid, machine=None):
        yield machine

    async def session(host, backend, *rest):
        pass

    monkeypatch.setattr(winusb, "bound", fake_bound)
    monkeypatch.setattr(audio_io, "PyAudioWPatchBackend", lambda output_source=None: FakeBackend())
    monkeypatch.setattr(app, "run_session", session)
    args = types.SimpleNamespace(transport="usb:0bda:887b", output_source="x", address="AA")
    assert app.run(args, machine=object()) == 0
    assert capsys.readouterr().out == ""


def test_preflight_is_the_four_setup_stages():
    from podbridge import doctor

    assert doctor.PREFLIGHT == (doctor.admin_rights, doctor.usb_id, doctor.firmware_files, doctor.cable_devices)


def test_run_refuses_a_cable_speaker(preflight_usb_only, monkeypatch, capsys):
    from podbridge import audio_io, winusb

    class CableSpeaker(FakeBackend):
        def render_name(self):
            return "CABLE In 16ch (VB-Audio Virtual Cable)"

    monkeypatch.setattr(winusb, "bound", lambda *a, **k: pytest.fail("bound the driver"))
    monkeypatch.setattr(audio_io, "PyAudioWPatchBackend", lambda output_source=None: CableSpeaker())
    args = types.SimpleNamespace(transport="usb:0bda:887b", output_source=None, address="AA")
    assert app.run(args, machine=object()) == 1
    assert "default speaker" in capsys.readouterr().err


def test_discord_hint_names_input_output_and_devices_to_ignore():
    lines = app.DISCORD_HINT.splitlines()
    assert len(lines) > 1
    assert '"CABLE Output"' in app.DISCORD_HINT
    assert '"Default"' in app.DISCORD_HINT
    assert "CABLE Input" in app.DISCORD_HINT and "CABLE In 16ch" in app.DISCORD_HINT


def test_serve_retries_through_retry_serve_with_bonded_connect(monkeypatch):
    from podbridge import bond, retry

    seen = {}

    async def fake_retry(once, reporter, sleep):
        seen["once"] = once
        seen["reporter"] = reporter

    async def fake_connect(host_, address, reporter):
        seen["connect"] = (host_, address, reporter)
        raise ConnectionError("stop")

    monkeypatch.setattr(retry, "retry_serve", fake_retry)
    monkeypatch.setattr(bond, "connect_bonded", fake_connect)
    reporter = object()

    async def go():
        await app.serve("host", "AA", app.Link(asyncio.Queue(), reporter))
        with pytest.raises(ConnectionError):
            await seen["once"]()

    asyncio.run(go())
    assert seen["reporter"] is reporter
    assert seen["connect"] == ("host", "AA", reporter)


def test_run_session_stops_when_pairing_times_out(monkeypatch):
    from podbridge import pairing, radio

    calls = []

    class H:
        async def close(self):
            calls.append("close")

    async def start(host, reporter, **kw):
        calls.append("radio")

    async def pair(host, reporter, *rest):
        return None

    monkeypatch.setattr(radio, "start_radio", start)
    monkeypatch.setattr(pairing, "pair_with_hint", pair)
    asyncio.run(app.run_session(H(), FakeBackend(), app.Session(None, object())))
    assert calls == ["radio", "close"]


class RecordingReporter:
    def __init__(self):
        self.events, self.steps = [], []

    def emit(self, event):
        self.events.append(event.kind)

    def set_step(self, name, status):
        self.steps.append((name, status))


def test_run_session_reports_steps_and_the_discord_hint(monkeypatch):
    from podbridge import pairing, radio

    async def start(host, reporter, **kw):
        pass

    async def serve(host, address, link):
        raise ConnectionError("stop")

    monkeypatch.setattr(radio, "start_radio", start)
    monkeypatch.setattr(app, "serve", serve)
    reporter = RecordingReporter()

    async def close():
        pass

    with pytest.raises(ConnectionError):
        asyncio.run(app.run_session(types.SimpleNamespace(close=close), FakeBackend(), app.Session("AA", reporter)))
    assert ("radio", "done") in reporter.steps and ("pairing", "done") in reporter.steps
    assert "discord_hint" in reporter.events


def test_a_reporter_that_raises_is_ignored(monkeypatch):
    class Broken:
        def emit(self, event):
            raise RuntimeError("boom")

        def set_step(self, *args):
            raise RuntimeError("boom")

    safe = app.SafeReporter(Broken())
    safe.emit(app.Event("failed", "x"))
    safe.set_step("radio", "active")


def test_serve_once_gives_the_aap_session_settings_and_the_saved_ca_choice(monkeypatch):
    from podbridge import bond, settings

    seen = {}

    class Conn:
        def on(self, *a):
            pass

    class Fake:
        def __init__(self, connection, queue, **options):
            seen.update(options)

        async def run(self):
            pass

    async def connect(host, address, reporter):
        return Conn()

    async def stream(host, connection):
        pass

    monkeypatch.setattr(bond, "connect_bonded", connect)
    monkeypatch.setattr(app, "start_stream", stream)
    monkeypatch.setattr(app, "AapSession", Fake)
    monkeypatch.setattr(settings, "load_ca_choice", lambda: 2)
    state = settings.SettingsState()
    reporter = RecordingReporter()
    asyncio.run(app.serve_once("h", "AA", app.Link(asyncio.Queue(), reporter, state)))
    assert seen["ca_choice"] == 2 and seen["on_event"] == state.apply and seen["reporter"] is reporter
    assert ("connected", "active") in reporter.steps
