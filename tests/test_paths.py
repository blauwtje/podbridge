import hashlib
import sys
from pathlib import Path

import pytest

from podbridge import app, paths, winusb

ROOT = Path(__file__).resolve().parents[1]


def installed(tmp_path: Path) -> Path:
    """A folder laid out like the installer's {app}: firmware and wdi-simple.exe."""
    install = tmp_path / "Program Files" / "podbridge"
    (install / "firmware").mkdir(parents=True)
    (install / "tools").mkdir()
    (install / "tools" / "wdi-simple.exe").write_bytes(b"MZ")
    return install


def test_source_run_without_override_uses_the_source_tree(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv(paths.INSTALL_DIR_ENV, raising=False)
    assert paths.install_dir() == ROOT


def test_source_run_uses_the_install_named_by_the_env_var(monkeypatch, tmp_path):
    install = installed(tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv(paths.INSTALL_DIR_ENV, str(install))
    assert paths.install_dir() == install


def test_frozen_exe_ignores_the_env_var(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "podbridge.exe"))
    monkeypatch.setenv(paths.INSTALL_DIR_ENV, str(tmp_path / "elsewhere"))
    assert paths.install_dir() == tmp_path / "app"


def test_source_run_reads_firmware_and_tools_from_the_install(monkeypatch, tmp_path):
    install = installed(tmp_path)
    firmware = b"Realtech" + b"x" * 64
    (install / "firmware" / app.FIRMWARE_FILE).write_bytes(firmware)
    monkeypatch.setattr(app, "FIRMWARE_SHA256", hashlib.sha256(firmware).hexdigest())
    monkeypatch.setattr(app, "download", lambda url: pytest.fail(f"downloaded {url}"))
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv(paths.INSTALL_DIR_ENV, str(install))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    assert paths.firmware_dir() == install / "firmware"
    assert app.ensure_firmware() == install / "firmware"
    machine = winusb.Machine()
    assert machine.wdi_simple == install / "tools" / "wdi-simple.exe"
    assert machine.state_path == install / "state" / "driver-state.json"
    assert machine.wdi_dest == install / "usb_driver"
