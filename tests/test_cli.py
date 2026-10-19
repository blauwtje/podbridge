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
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert paths.firmware_dir() == paths.install_dir() / "firmware"
    (tmp_path / "podbridge" / "firmware").mkdir(parents=True)
    (tmp_path / "podbridge" / "firmware" / "rtl8852bu_fw.bin").write_bytes(b"x")
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
