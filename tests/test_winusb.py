import argparse
import json
import subprocess
from pathlib import Path

import pytest

from podbridge import winusb

VID, PID = 0x0BDA, 0x887B
INSTANCE = r"USB\VID_0BDA&PID_887B\00E04C000001"
REALTEK_INF = "oem12.inf"
LIBWDI_INF = "oem42.inf"
OTHER_INF = "oem3.inf"
OTHER_INSTANCE = r"USB\VID_1234&PID_0001\X"
PROVIDERS = {REALTEK_INF: "Realtek", LIBWDI_INF: "libwdi", OTHER_INF: "Contoso"}


def driver_store(providers):
    return "Microsoft PnP Utility\n\n" + "\n\n".join(
        f"Published Name:     {inf}\nOriginal Name:      x.inf\nProvider Name:      {provider}\nClass Name:         USB"
        for inf, provider in providers.items()
    )


def device_block(instance_id, driver):
    lines = [
        f"Instance ID:                {instance_id}",
        "Device Description:         Realtek Bluetooth Adapter",
        "Status:                     Started",
    ]
    if driver:
        lines.append(f"Driver Name:                {driver}")
        lines += ["Matching Drivers:", f"    Driver Name:            {driver}", "    Original Name:          x.inf"]
    return "\n".join(lines)


class FakeWindows:
    """Stands in for wdi-simple, pnputil and powershell at the runner boundary."""

    def __init__(self, state_path, driver=REALTEK_INF):
        self.state_path = state_path
        self.original = REALTEK_INF
        self.driver = driver
        self.calls = []
        self.wdi_exit = 0
        self.wdi_binds = True
        self.scan_rebinds = True
        self.delete_unbinds = True
        self.state_at_wdi = None
        self.providers = dict(PROVIDERS)

    def __call__(self, args):
        self.calls.append(list(args))
        name = Path(args[0]).name.lower()
        if name == "wdi-simple.exe":
            return self.wdi(args)
        if name == "pnputil":
            return self.pnputil(args[1:])
        return subprocess.CompletedProcess(args, 0, "", "")

    def wdi(self, args):
        self.state_at_wdi = json.loads(self.state_path.read_text()) if self.state_path.exists() else None
        if self.wdi_exit == 0 and self.wdi_binds:
            self.driver = LIBWDI_INF
        return subprocess.CompletedProcess(args, self.wdi_exit, "", "")

    def pnputil(self, rest):
        out = ""
        if rest == ["/enum-devices", "/connected"]:
            out = "Microsoft PnP Utility\n\n" + device_block(OTHER_INSTANCE, OTHER_INF) + "\n\n" + device_block(INSTANCE, self.driver)
        elif rest[:2] == ["/enum-devices", "/instanceid"]:
            out = self.instance(rest[2])
        elif rest == ["/enum-drivers"]:
            out = driver_store(self.providers)
        elif rest[0] == "/delete-driver" and rest[1] == self.driver and self.delete_unbinds:
            self.driver = None
        elif rest == ["/scan-devices"] and self.driver is None and self.scan_rebinds:
            self.driver = self.original
        return subprocess.CompletedProcess(["pnputil", *rest], 0, out, "")

    def instance(self, instance_id):
        if instance_id == INSTANCE:
            return device_block(INSTANCE, self.driver)
        if instance_id == OTHER_INSTANCE:
            return device_block(OTHER_INSTANCE, OTHER_INF)
        return "No devices were found on the system."

    def ran(self, *args):
        return any(c[: len(args)] == list(args) for c in self.calls)


@pytest.fixture
def setup(tmp_path):
    state_path = tmp_path / "state" / "driver-state.json"
    fake = FakeWindows(state_path)
    machine = winusb.Machine(
        runner=fake,
        state_path=state_path,
        wdi_simple=tmp_path / "tools" / "wdi-simple.exe",
        sleep=lambda s: None,
    )
    return fake, machine, state_path


def write_state(path, **state):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"instance_id": INSTANCE, "original_inf": REALTEK_INF, "libwdi_inf": LIBWDI_INF}
    data.update(state)
    path.write_text(json.dumps(data))


def test_bind_records_state_before_wdi_simple_and_restores_on_exit(setup):
    fake, machine, state_path = setup
    with winusb.bound(VID, PID, machine):
        assert fake.driver == LIBWDI_INF
        assert json.loads(state_path.read_text()) == {
            "instance_id": INSTANCE, "original_inf": REALTEK_INF, "libwdi_inf": LIBWDI_INF,
        }
    assert fake.state_at_wdi == {"instance_id": INSTANCE, "original_inf": REALTEK_INF, "libwdi_inf": None}
    wdi = next(c for c in fake.calls if c[0].endswith("wdi-simple.exe"))
    assert wdi[1:] == ["--vid", "0x0BDA", "--pid", "0x887B", "--type", "0", "--silent"]
    assert fake.ran("pnputil", "/delete-driver", LIBWDI_INF, "/uninstall", "/force")
    assert fake.ran("pnputil", "/scan-devices")
    assert fake.driver == REALTEK_INF
    assert not state_path.exists()


def test_error_inside_block_still_restores(setup):
    fake, machine, state_path = setup
    with pytest.raises(RuntimeError):
        with winusb.bound(VID, PID, machine):
            raise RuntimeError("boom")
    assert fake.driver == REALTEK_INF
    assert not state_path.exists()


def test_leftover_state_restores_before_bind(setup):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver = LIBWDI_INF
    with winusb.bound(VID, PID, machine):
        pass
    delete = fake.calls.index(["pnputil", "/delete-driver", LIBWDI_INF, "/uninstall", "/force"])
    wdi = next(i for i, c in enumerate(fake.calls) if c[0].endswith("wdi-simple.exe"))
    assert delete < wdi
    assert fake.driver == REALTEK_INF


def test_wdi_simple_failure_raises_and_leaves_realtek_driver(setup):
    fake, machine, state_path = setup
    fake.wdi_exit = 5
    with pytest.raises(winusb.DriverError, match="wdi-simple"):
        with winusb.bound(VID, PID, machine):
            pytest.fail("body must not run")
    assert not fake.ran("pnputil", "/delete-driver")
    assert fake.driver == REALTEK_INF
    assert not state_path.exists()


def test_bind_poll_gives_up_after_ten_seconds(setup):
    fake, machine, state_path = setup
    fake.wdi_binds = False
    slept = []
    machine.sleep = slept.append
    with pytest.raises(winusb.DriverError, match="WinUSB"):
        with winusb.bound(VID, PID, machine):
            pytest.fail("body must not run")
    assert sum(slept) == pytest.approx(10)


def test_restore_keeps_state_when_device_stays_on_winusb(setup):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver = LIBWDI_INF
    fake.delete_unbinds = False
    with pytest.raises(winusb.DriverError, match="still"):
        winusb.restore_driver(machine)
    assert state_path.exists()


def test_restore_main_without_state_is_a_no_op(setup):
    fake, machine, state_path = setup
    assert winusb.restore_main(argparse.Namespace(remove_cert=False), machine) == 0
    assert fake.calls == []


def test_restore_main_remove_cert(setup):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver = LIBWDI_INF
    assert winusb.restore_main(argparse.Namespace(remove_cert=True), machine) == 0
    assert fake.driver == REALTEK_INF
    ps = [c for c in fake.calls if Path(c[0]).name.lower() == "powershell.exe"]
    assert len(ps) == 1
    script = ps[0][-1]
    for part in ("Cert:\\LocalMachine\\", "'Root'", "'TrustedPublisher'", "*libwdi autogenerated*", "Remove-Item"):
        assert part in script


def test_restore_main_without_flag_keeps_cert(setup):
    fake, machine, state_path = setup
    winusb.restore_main(argparse.Namespace(remove_cert=False), machine)
    winusb.restore_main(argparse.Namespace(), machine)
    assert not any(Path(c[0]).name.lower() == "powershell.exe" for c in fake.calls)


@pytest.mark.parametrize("field,value", [
    ("libwdi_inf", 'oem42.inf" & calc.exe & "'),
    ("libwdi_inf", "..\\..\\Windows\\INF\\usb.inf"),
    ("instance_id", 'USB\\VID_0BDA&PID_887B\\X" /force & del C:\\'),
])
def test_injection_in_state_file_is_rejected_before_any_command(setup, field, value):
    fake, machine, state_path = setup
    write_state(state_path, **{field: value})
    assert winusb.restore_main(argparse.Namespace(remove_cert=False), machine) == 1
    assert fake.calls == []
    assert state_path.exists()


def test_corrupt_state_fails_closed(setup, capsys):
    fake, machine, state_path = setup
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not json")
    with pytest.raises(winusb.DriverError):
        with winusb.bound(VID, PID, machine):
            pytest.fail("body must not run")
    assert fake.calls == []
    assert winusb.restore_main(argparse.Namespace(remove_cert=False), machine) == 1
    assert "driver-state.json" in capsys.readouterr().err


def test_runner_timeout_fails_closed_before_wdi_simple(setup):
    fake, machine, state_path = setup

    def timing_out(args):
        raise subprocess.TimeoutExpired(args, 60)

    machine.runner = timing_out
    with pytest.raises(subprocess.TimeoutExpired):
        with winusb.bound(VID, PID, machine):
            pytest.fail("body must not run")
    assert not state_path.exists()


def test_state_file_lives_in_the_admin_only_install_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(winusb, "install_dir", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    assert winusb.Machine().state_path == tmp_path / "state" / "driver-state.json"


def test_state_naming_a_driver_the_device_does_not_use_is_not_deleted(setup):
    fake, machine, state_path = setup
    write_state(state_path, libwdi_inf=OTHER_INF)
    fake.driver = REALTEK_INF
    with pytest.raises(winusb.DriverError, match="libwdi"):
        winusb.restore_driver(machine)
    assert not fake.ran("pnputil", "/delete-driver")
    assert fake.driver == REALTEK_INF
    assert state_path.exists()


@pytest.mark.parametrize("providers", [
    {**PROVIDERS, LIBWDI_INF: "Contoso"},
    {REALTEK_INF: "Realtek"},
])
def test_restore_refuses_an_inf_whose_provider_is_not_libwdi(setup, providers):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver = LIBWDI_INF
    fake.providers = providers
    assert winusb.restore_main(argparse.Namespace(remove_cert=False), machine) == 1
    assert not fake.ran("pnputil", "/delete-driver")
    assert not fake.ran("pnputil", "/scan-devices")
    assert fake.driver == LIBWDI_INF
    assert state_path.exists()


def test_provider_lookup_failure_fails_closed(setup):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver = LIBWDI_INF
    inner = machine.runner

    def store_fails(args):
        if args[1:] == ["/enum-drivers"]:
            return subprocess.CompletedProcess(args, 1, "", "access denied")
        return inner(args)

    machine.runner = store_fails
    with pytest.raises(winusb.DriverError, match="enum-drivers"):
        winusb.restore_driver(machine)
    assert not fake.ran("pnputil", "/delete-driver")
    assert state_path.exists()


def test_state_for_a_non_realtek_device_is_rejected_before_any_command(setup):
    fake, machine, state_path = setup
    write_state(state_path, instance_id=OTHER_INSTANCE, libwdi_inf=OTHER_INF)
    assert winusb.restore_main(argparse.Namespace(remove_cert=False), machine) == 1
    assert fake.calls == []
    assert state_path.exists()


def test_bind_refuses_a_device_outside_the_rtk_table(setup):
    fake, machine, state_path = setup
    with pytest.raises(winusb.DriverError, match="rtk"):
        with winusb.bound(0x1234, 0x0001, machine):
            pytest.fail("body must not run")
    assert fake.calls == []
    assert not state_path.exists()


def test_state_naming_realtek_as_libwdi_is_not_deleted(setup):
    fake, machine, state_path = setup
    write_state(state_path, original_inf=REALTEK_INF, libwdi_inf=REALTEK_INF)
    with pytest.raises(winusb.DriverError):
        winusb.restore_driver(machine)
    assert not fake.ran("pnputil", "/delete-driver")
    assert fake.driver == REALTEK_INF


def test_symlinked_state_file_is_refused(setup, tmp_path):
    fake, machine, state_path = setup
    target = tmp_path / "protected.txt"
    target.write_text("keep me")
    state_path.parent.mkdir(parents=True)
    state_path.symlink_to(target)
    with pytest.raises(winusb.DriverError, match="link"):
        with winusb.bound(VID, PID, machine):
            pytest.fail("body must not run")
    assert target.read_text() == "keep me"
    assert fake.calls == []
