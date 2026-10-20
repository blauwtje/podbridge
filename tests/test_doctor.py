import asyncio
import json

import pytest

from podbridge import doctor, paths
from podbridge.audio_io import FakeBackend
from podbridge.selftest import tone


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def run(stages, ctx=None):
    return asyncio.run(doctor.run_stages(stages, ctx or doctor.Context()))


def make(name, outcome=None, calls=None):
    """A stage that raises `outcome` when it is an exception, else returns it."""

    async def stage(ctx):
        if calls is not None:
            calls.append(name)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    stage.__doc__ = name
    return stage


def test_stops_at_first_failure_and_logs(capsys, appdata):
    calls = []
    stages = [
        make("one", "detail", calls),
        make("two", doctor.StageFailed("do this"), calls),
        make("three", calls=calls),
    ]
    assert run(stages) is False
    assert calls == ["one", "two"]
    assert capsys.readouterr().out.splitlines() == ["PASS one: detail", "FAIL two: do this"]
    lines = (appdata / "podbridge" / "doctor.log").read_text().splitlines()
    assert [line.split(" ", 1)[1] for line in lines] == ["PASS one: detail", "FAIL two: do this"]


def test_unexpected_error_is_a_failure(capsys):
    assert run([make("boom", ValueError("bad"))]) is False
    assert capsys.readouterr().out.strip() == "FAIL boom: ValueError: bad"


def test_all_pass_appends_to_log(appdata):
    assert run([make("one")]) is True
    assert run([make("one")]) is True
    assert len((appdata / "podbridge" / "doctor.log").read_text().splitlines()) == 2


def test_stack_unwinds_on_failure():
    closed = []
    ctx = doctor.Context()
    ctx.stack.callback(closed.append, "restored")
    assert run([make("x", doctor.StageFailed("h"))], ctx) is False
    assert closed == ["restored"]


def test_stage_order_follows_decision_17():
    assert [s.__doc__ for s in doctor.STAGES] == [
        "admin rights",
        "Realtek USB ID present",
        "firmware files present",
        "WinUSB bind",
        "Bumble transport and firmware load",
        "bond present",
        "A2DP codec chosen",
        "AAP mic frames per second",
        "VB-CABLE devices present",
        "loopback audio while muted",
    ]


def test_firmware_stage_names_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "firmware_dir", lambda: tmp_path)
    assert run([doctor.firmware_files]) is False
    (tmp_path / "rtl8852bu_fw.bin").write_bytes(b"x")
    (tmp_path / "rtl8852bu_config.bin").write_bytes(b"x")
    assert run([doctor.firmware_files]) is True


def test_bond_stage(appdata):
    base = appdata / "podbridge"
    base.mkdir()
    assert run([doctor.bond_present]) is False
    (base / "device.json").write_text(json.dumps({"address": "AA:BB:CC:DD:EE:FF"}))
    (base / "keys.json").write_text(json.dumps({"ns": {}}))
    assert run([doctor.bond_present]) is False
    (base / "keys.json").write_text(json.dumps({"ns": {"AA:BB:CC:DD:EE:FF/P": {}}}))
    assert run([doctor.bond_present]) is True


def test_cable_stage(monkeypatch):
    monkeypatch.setattr(doctor, "cable_endpoints", lambda: {"CABLE Input"})
    assert run([doctor.cable_devices]) is False
    monkeypatch.setattr(doctor, "cable_endpoints", lambda: {"CABLE Input", "CABLE Output"})
    assert run([doctor.cable_devices]) is True


def test_cable_hint_says_restart_once_then_run_the_setup_again(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "cable_endpoints", lambda: {"CABLE Input"})
    assert run([doctor.cable_devices]) is False
    out = capsys.readouterr().out
    assert "restart Windows once after setup" in out
    assert "run the podbridge setup again" in out


def test_quiet_run_prints_and_logs_nothing_on_a_pass(capsys, appdata):
    assert run([make("one")], None) is True
    capsys.readouterr()
    assert asyncio.run(doctor.run_stages([make("one")], doctor.Context(), quiet=True)) is True
    assert capsys.readouterr().out == ""
    assert len((appdata / "podbridge" / "doctor.log").read_text().splitlines()) == 1


@pytest.mark.parametrize("loopback, expected", [(tone(), True), (b"", False)])
def test_loopback_stage_needs_nonzero_samples_and_restores_mute(loopback, expected):
    backend = FakeBackend(loopback=loopback, muted=False)
    ctx = doctor.Context(backend_factory=lambda: backend)
    assert run([doctor.loopback_while_muted], ctx) is expected
    assert backend.muted is False and backend.closed
    assert backend.played and backend.played[0][1] == tone()


def test_firmware_stage_config_file_is_optional(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "firmware_dir", lambda: tmp_path)
    (tmp_path / "rtl8852bu_fw.bin").write_bytes(b"x")
    assert run([doctor.firmware_files]) is True


def test_loopback_tone_goes_to_the_captured_device():
    class Headset(FakeBackend):
        def render_name(self):
            return "Headset"

    backend = Headset(loopback=tone())
    assert run([doctor.loopback_while_muted], doctor.Context(backend_factory=lambda: backend)) is True
    assert backend.played[0][0] == "Headset"
