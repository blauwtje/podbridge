import subprocess
import sys

import pytest

from podbridge import __main__ as cli, paths


def test_version():
    out = subprocess.run([sys.executable, "-m", "podbridge", "--version"], capture_output=True, text=True)
    assert out.returncode == 0
    assert out.stdout.startswith("podbridge ")


def test_subcommands():
    for name in ("run", "pair", "firmware", "doctor", "restore", "selftest"):
        assert cli.build_parser().parse_args([name]).command == name


def test_unknown_command_rejected():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["nope"])


def test_lazy_dispatch(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "podbridge.doctor", type(sys)("podbridge.doctor"))
    sys.modules["podbridge.doctor"].main = lambda args: calls.append(args.command) or 3
    assert cli.main(["doctor"]) == 3
    assert calls == ["doctor"]


def test_firmware_dir_prefers_downloaded(tmp_path, monkeypatch):
    import hashlib

    from podbridge import app

    firmware = b"Realtech" + b"x" * 64
    monkeypatch.setattr(app, "FIRMWARE_SHA256", hashlib.sha256(firmware).hexdigest())
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert paths.firmware_dir() == paths.install_dir() / "firmware"
    (tmp_path / "podbridge" / "firmware").mkdir(parents=True)
    (tmp_path / "podbridge" / "firmware" / "rtl8852bu_fw.bin").write_bytes(firmware)
    assert paths.firmware_dir() == tmp_path / "podbridge" / "firmware"


def test_restore_command_names_restore_main():
    from podbridge import __main__ as cli

    assert cli.COMMANDS["restore"] == ("podbridge.winusb", "restore_main")


def test_pause_shows_the_error_before_waiting(monkeypatch, capsys):
    from podbridge import __main__

    def boom(args):
        raise RuntimeError("bind failed")

    seen = []
    monkeypatch.setitem(__main__.COMMANDS, "run", ("podbridge.__main__", "_boom"))
    monkeypatch.setattr(__main__, "_boom", boom, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: seen.append(capsys.readouterr().err))
    assert __main__.main(["run", "--pause"]) == 1
    assert "bind failed" in seen[0]


def _fake_run(monkeypatch, started):
    calls = []
    monkeypatch.setattr(cli.update, "apply", lambda args: started)
    monkeypatch.setitem(sys.modules, "podbridge.app", type(sys)("podbridge.app"))
    sys.modules["podbridge.app"].run = lambda args: calls.append(args) or 5
    return calls


def test_run_stops_without_pause_when_update_started(monkeypatch):
    calls = _fake_run(monkeypatch, True)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("paused"))
    assert cli.main(["run", "--pause"]) == 0
    assert calls == []


def test_run_continues_when_no_update(monkeypatch):
    calls = _fake_run(monkeypatch, False)
    assert cli.main(["run"]) == 5
    assert len(calls) == 1


def test_no_update_flag_only_on_run():
    assert cli.build_parser().parse_args(["run", "--no-update"]).no_update is True
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["pair", "--no-update"])
