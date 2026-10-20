import asyncio
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


def test_firmware_downloads_to_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    urls = []
    monkeypatch.setattr(app, "download", lambda url: urls.append(url) or b"x")
    assert app.firmware(None) == 0
    assert [u.rsplit("/", 1)[1] for u in urls] == [app.FIRMWARE_FILE, app.FIRMWARE_CONFIG]
    assert paths.firmware_dir() == tmp_path / "podbridge" / "firmware"
    assert (paths.firmware_dir() / app.FIRMWARE_FILE).read_bytes() == b"x"


def test_firmware_config_is_optional_but_firmware_is_not(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    def only_firmware(url):
        if url.endswith(app.FIRMWARE_CONFIG):
            raise OSError("404")
        return b"x"

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

    async def session(host, backend, address):
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


def test_run_restores_driver_around_session(preflight_usb_only, monkeypatch):
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

    async def failing_session(host, backend, address):
        events.append("session")
        raise RuntimeError("boom")

    monkeypatch.setattr(winusb, "bound", fake_bound)
    monkeypatch.setattr(audio_io, "PyAudioWPatchBackend", lambda output_source=None: FakeBackend())
    monkeypatch.setattr(app, "run_session", failing_session)
    args = types.SimpleNamespace(transport="usb:0bda:887b", output_source="x", address="AA:BB:CC:DD:EE:FF")
    with pytest.raises(RuntimeError):
        app.run(args, machine=object())
    assert events == [("bind", 0x0BDA, 0x887B), "session", "restore"]


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

    async def session(host, backend, address):
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
