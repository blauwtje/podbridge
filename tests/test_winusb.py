import argparse
import dataclasses
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
        self.wdi_stdout = ""
        self.wdi_stderr = ""
        self.wdi_binds = True
        self.scan_rebinds = True
        self.delete_unbinds = True
        self.delete_exit = 0
        self.state_at_wdi = None
        self.providers = dict(PROVIDERS)
        self.restart_exit = 0
        self.restart_driver = None  # the driver a /restart-device leaves bound; None keeps the current one

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
            self.providers[LIBWDI_INF] = "libwdi"
        return subprocess.CompletedProcess(args, self.wdi_exit, self.wdi_stdout, self.wdi_stderr)

    def pnputil(self, rest):
        out = ""
        if rest == ["/enum-devices", "/connected"]:
            out = "Microsoft PnP Utility\n\n" + device_block(OTHER_INSTANCE, OTHER_INF) + "\n\n" + device_block(INSTANCE, self.driver)
        elif rest[:2] == ["/enum-devices", "/instanceid"]:
            out = self.instance(rest[2])
        elif rest == ["/enum-drivers"]:
            out = driver_store(self.providers)
        elif rest[0] == "/delete-driver":
            self.providers.pop(rest[1], None)
            if rest[1] == self.driver and self.delete_unbinds:
                self.driver = None
            if self.delete_exit:
                return subprocess.CompletedProcess(["pnputil", *rest], self.delete_exit, "Driver package deleted successfully.\nSystem reboot is needed", "")
        elif rest[0] == "/restart-device":
            if self.restart_exit:
                return subprocess.CompletedProcess(["pnputil", *rest], self.restart_exit, "", "Restart failed")
            self.driver = self.restart_driver or self.driver
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
        wdi_dest=tmp_path / "usb_driver",
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
    assert wdi[1:] == ["--vid", "0x0BDA", "--pid", "0x887B", "--type", "0", "--dest", str(machine.wdi_dest)]
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


def test_restore_after_the_libwdi_inf_is_already_deleted(setup):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver = LIBWDI_INF
    fake.scan_rebinds = False
    with pytest.raises(winusb.DriverError, match="still uses WinUSB"):
        winusb.restore_driver(machine)
    assert LIBWDI_INF not in fake.providers
    assert state_path.exists()
    fake.driver = REALTEK_INF
    assert winusb.restore_driver(machine) is False
    assert not state_path.exists()


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


def test_bind_gives_wdi_simple_an_absolute_dest_in_the_install_folder(setup):
    fake, machine, state_path = setup
    with winusb.bound(VID, PID, machine):
        pass
    call = next(c for c in fake.calls if Path(c[0]).name == "wdi-simple.exe")
    dest = Path(call[call.index("--dest") + 1])
    assert dest.is_absolute()
    assert dest == machine.wdi_dest
    assert "--silent" not in call


def test_default_dest_is_under_the_admin_only_install_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(winusb, "install_dir", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    assert winusb.Machine().wdi_dest.parent == tmp_path


def test_bind_empties_a_leftover_dest_before_wdi_simple(setup):
    fake, machine, state_path = setup
    (machine.wdi_dest / "sub").mkdir(parents=True)
    (machine.wdi_dest / "old.inf").write_text("stale")
    seen = []
    original = fake.wdi
    fake.wdi = lambda args: (seen.append(sorted(p.name for p in machine.wdi_dest.iterdir())), original(args))[1]
    with winusb.bound(VID, PID, machine):
        pass
    assert seen == [[]]


def test_bind_replaces_a_leftover_file_at_the_dest(setup):
    fake, machine, state_path = setup
    machine.wdi_dest.write_text("not a folder")
    with winusb.bound(VID, PID, machine):
        assert machine.wdi_dest.is_dir()


def test_bind_refuses_a_linked_dest(setup, tmp_path):
    fake, machine, state_path = setup
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_text("x")
    try:
        machine.wdi_dest.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(winusb.DriverError, match="link"):
        with winusb.bound(VID, PID, machine):
            pytest.fail("body must not run")
    assert (target / "keep.txt").exists()
    assert not fake.ran(str(machine.wdi_simple))


def test_wdi_simple_failure_shows_its_own_output(setup):
    fake, machine, state_path = setup
    fake.wdi_exit = -3
    fake.wdi_stdout = "Extracting driver files...\n  Access denied\n"
    fake.wdi_stderr = "libwdi:error [check_dir] could not create usb_driver\n"
    with pytest.raises(winusb.DriverError) as info:
        with winusb.bound(VID, PID, machine):
            pytest.fail("body must not run")
    text = str(info.value)
    assert "Access denied" in text
    assert "could not create usb_driver" in text


def test_failure_output_keeps_only_the_last_lines(setup):
    fake, machine, state_path = setup
    fake.wdi_exit = 1
    fake.wdi_stdout = "\n".join(f"line {i}" for i in range(winusb.OUTPUT_TAIL_LINES + 5))
    with pytest.raises(winusb.DriverError) as info:
        winusb.run_checked(machine, [str(machine.wdi_simple), "--vid"])
    text = str(info.value)
    assert f"line {winusb.OUTPUT_TAIL_LINES + 4}" in text
    assert "line 4" not in text.split("\n")
    assert "line 5" in text.split("\n")


@pytest.mark.parametrize("returncode, shown", [
    (4294967293, "-3 (WDI_ERROR_ACCESS)"),
    (-3, "-3 (WDI_ERROR_ACCESS)"),
    (4294967282, "-14 (WDI_ERROR_USER_CANCEL)"),
    (-99, "-99 (WDI_ERROR_OTHER)"),
    (5, "5 (unknown libwdi code)"),
])
def test_wdi_exit_code_is_signed_and_named(setup, returncode, shown):
    fake, machine, state_path = setup
    fake.wdi_exit = returncode
    with pytest.raises(winusb.DriverError) as info:
        with winusb.bound(VID, PID, machine):
            pytest.fail("body must not run")
    assert f"exit code {shown}" in str(info.value)


def test_unsigned_exit_code_keeps_its_raw_value(setup):
    fake, machine, state_path = setup
    fake.wdi_exit = 4294967293
    with pytest.raises(winusb.DriverError, match="raw 4294967293"):
        with winusb.bound(VID, PID, machine):
            pytest.fail("body must not run")


def test_other_tools_get_a_signed_code_without_a_libwdi_name(setup):
    fake, machine, state_path = setup
    machine.runner = lambda args: subprocess.CompletedProcess(args, 3, "", "")
    with pytest.raises(winusb.DriverError) as info:
        winusb.run_checked(machine, ["pnputil", "/scan-devices"])
    assert str(info.value) == "pnputil /scan-devices failed with exit code 3"


@pytest.mark.parametrize("code", [3010, 1641])
def test_reboot_exit_is_success_and_restore_finishes_when_device_is_back(setup, code, capsys):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver = LIBWDI_INF
    fake.delete_exit = code
    assert winusb.restore_driver(machine) is True
    assert fake.driver == REALTEK_INF
    assert not state_path.exists()


@pytest.mark.parametrize("code", [3010, 1641])
def test_reboot_exit_with_device_still_on_winusb_keeps_state_marked_pending(setup, code, capsys):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver = LIBWDI_INF
    fake.delete_exit = code
    fake.delete_unbinds = False
    assert winusb.restore_driver(machine) is True
    assert json.loads(state_path.read_text())["reboot_pending"] is True
    assert "restart windows" in capsys.readouterr().err.lower()


def test_pending_restore_finishes_cleanly_once_device_is_back_on_its_own_driver(setup):
    fake, machine, state_path = setup
    write_state(state_path, reboot_pending=True)
    fake.driver = REALTEK_INF
    fake.providers.pop(LIBWDI_INF)
    winusb.restore_driver(machine)
    assert not state_path.exists()
    assert not fake.ran("pnputil", "/delete-driver", LIBWDI_INF, "/uninstall", "/force")


def test_pending_restore_before_the_reboot_asks_for_restart_and_keeps_state(setup):
    fake, machine, state_path = setup
    write_state(state_path, reboot_pending=True)
    fake.driver = LIBWDI_INF
    with pytest.raises(winusb.DriverError, match="(?i)restart"):
        winusb.restore_driver(machine)
    assert state_path.exists()


def test_other_nonzero_delete_exit_stays_an_error(setup):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver = LIBWDI_INF
    fake.delete_exit = 5
    with pytest.raises(winusb.DriverError, match="exit code 5"):
        winusb.restore_driver(machine)


def test_restore_error_does_not_replace_the_error_already_leaving_bound(setup, capsys):
    fake, machine, state_path = setup
    fake.delete_exit = 5
    with pytest.raises(RuntimeError, match="firmware") as info:
        with winusb.bound(VID, PID, machine):
            raise RuntimeError("firmware")
    shown = capsys.readouterr().err + "".join(getattr(info.value, "__notes__", []))
    assert "exit code 5" in shown


def test_reboot_exit_inside_bound_does_not_hide_the_firmware_error(setup, capsys):
    fake, machine, state_path = setup
    fake.delete_exit = 3010
    fake.delete_unbinds = False
    with pytest.raises(RuntimeError, match="firmware"):
        with winusb.bound(VID, PID, machine):
            raise RuntimeError("firmware")
    assert json.loads(state_path.read_text())["reboot_pending"] is True
    assert "restart windows" in capsys.readouterr().err.lower()


def test_restart_device_restarts_the_bound_instance_and_keeps_winusb(setup):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver = LIBWDI_INF
    winusb.restart_device(machine)
    assert fake.ran("pnputil", "/restart-device", INSTANCE)
    assert fake.driver == LIBWDI_INF and state_path.exists()


def test_restart_device_without_a_bind_runs_nothing(setup):
    fake, machine, state_path = setup
    with pytest.raises(winusb.DriverError):
        winusb.restart_device(machine)
    write_state(state_path, libwdi_inf=None)
    with pytest.raises(winusb.DriverError):
        winusb.restart_device(machine)
    assert not fake.ran("pnputil", "/restart-device")


def test_restart_device_failure_is_a_driver_error_with_its_output(setup):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver, fake.restart_exit = LIBWDI_INF, 1
    with pytest.raises(winusb.DriverError, match="Restart failed"):
        winusb.restart_device(machine)


@pytest.mark.parametrize("code", sorted(winusb.REBOOT_CODES))
def test_restart_device_that_needs_a_reboot_is_not_success(setup, code):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver, fake.restart_exit = LIBWDI_INF, code
    with pytest.raises(winusb.DriverError, match="restart"):
        winusb.restart_device(machine)


def test_restart_device_that_loses_the_winusb_bind_is_an_error(setup):
    fake, machine, state_path = setup
    write_state(state_path)
    fake.driver, fake.restart_driver = LIBWDI_INF, REALTEK_INF
    with pytest.raises(winusb.DriverError, match="WinUSB"):
        winusb.restart_device(machine)


class FakeLock:
    """The named mutex: one holder at a time, shared by every machine given the same instance."""

    def __init__(self):
        self.held = False

    def __call__(self):
        if self.held:
            raise winusb.AlreadyRunning(winusb.ALREADY_RUNNING)
        self.held = True
        return self.release

    def release(self):
        self.held = False


def machine_with(machine, lock):
    return dataclasses.replace(machine, single_instance=lock)


def test_second_instance_is_refused_before_it_touches_the_driver(setup):
    fake, machine, _ = setup
    machine = machine_with(machine, FakeLock())
    with winusb.bound(VID, PID, machine):
        calls_before = len(fake.calls)
        with pytest.raises(winusb.DriverError, match="another podbridge is running"):
            with winusb.bound(VID, PID, machine):
                pytest.fail("the second instance got the device")
        assert len(fake.calls) == calls_before
        assert fake.driver == LIBWDI_INF  # the first instance still has WinUSB


def test_lock_is_released_after_a_normal_run_and_after_a_failed_one(setup):
    fake, machine, _ = setup
    lock = FakeLock()
    machine = machine_with(machine, lock)
    with winusb.bound(VID, PID, machine):
        pass
    assert not lock.held
    with pytest.raises(RuntimeError, match="boom"):
        with winusb.bound(VID, PID, machine):
            raise RuntimeError("boom")
    assert not lock.held
    with winusb.bound(VID, PID, machine):  # a normal single run is never blocked by an earlier one
        assert lock.held


def test_lock_is_released_when_the_first_restore_fails(setup):
    fake, machine, state_path = setup
    lock = FakeLock()
    machine = machine_with(machine, lock)
    write_state(state_path)
    fake.driver = LIBWDI_INF
    fake.delete_exit = 5
    with pytest.raises(winusb.DriverError):
        with winusb.bound(VID, PID, machine):
            pass
    assert not lock.held


def test_default_machine_uses_the_named_mutex_of_the_app(setup):
    assert winusb.Machine().single_instance is winusb.hold_single_instance
    assert winusb.MUTEX_NAME == "Global\\podbridge"
