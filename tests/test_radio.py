import asyncio

import pytest

from podbridge import radio


class Rec:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class Host:
    def __init__(self, hangs):
        self.hangs, self.calls, self.closed = hangs, 0, 0

    async def start(self):
        self.calls += 1
        if self.calls <= self.hangs:
            await asyncio.sleep(10)
        return "dev"

    async def close(self):
        self.closed += 1


def test_first_start_ok():
    host, rec = Host(0), Rec()
    assert asyncio.run(radio.start_radio(host, rec, timeout=0.05)) == "dev"
    assert rec.events == []


def recorder(log, name, error=None):
    def step():
        log.append(name)
        if error is not None:
            raise error

    return step


def test_timeout_runs_the_first_recovery_step_and_retries():
    host, rec, log = Host(1), Rec(), []
    recover = [("resetting A", recorder(log, "a")), ("restarting B", recorder(log, "b"))]
    assert asyncio.run(radio.start_radio(host, rec, timeout=0.05, recover=recover)) == "dev"
    assert (host.closed, host.calls, log) == (1, 2, ["a"])
    assert [e.kind for e in rec.events] == ["radio_reset"]
    assert "resetting A" in rec.events[0].message


def test_each_timeout_runs_the_next_recovery_step():
    host, rec, log = Host(2), Rec(), []
    recover = [("resetting A", recorder(log, "a")), ("restarting B", recorder(log, "b"))]
    assert asyncio.run(radio.start_radio(host, rec, timeout=0.05, recover=recover)) == "dev"
    assert (host.closed, host.calls, log) == (2, 3, ["a", "b"])
    assert [e.kind for e in rec.events] == ["radio_reset", "radio_reset"]


def test_no_answer_after_every_step_raises_naming_each_step_and_the_next_step():
    host, rec, log = Host(3), Rec(), []
    recover = [("resetting A", recorder(log, "a")), ("restarting B", recorder(log, "b"))]
    with pytest.raises(radio.RadioError) as caught:
        asyncio.run(radio.start_radio(host, rec, timeout=0.05, recover=recover))
    assert [e.kind for e in rec.events] == ["radio_reset", "radio_reset", "radio_timeout"]
    assert (host.closed, host.calls, log) == (3, 3, ["a", "b"])
    message = str(caught.value)
    assert "resetting A" in message and "restarting B" in message
    # a warm Windows restart did not revive this radio in the field, so the advice is a full shutdown
    assert "shutdown /s /full" in caught.value.next_step
    assert rec.events[-1].next_step == caught.value.next_step
    assert "reboot" not in message.lower() and "unplug" not in caught.value.next_step.lower()


def test_a_failing_step_is_reported_and_the_next_step_runs_without_a_retry_between():
    from podbridge.winusb import DriverError

    host, rec, log = Host(1), Rec(), []
    recover = [("resetting A", recorder(log, "a", DriverError("no driver-state.json"))),
               ("restarting B", recorder(log, "b"))]
    assert asyncio.run(radio.start_radio(host, rec, timeout=0.05, recover=recover)) == "dev"
    assert (host.calls, log) == (2, ["a", "b"])
    assert any("no driver-state.json" in e.message for e in rec.events)
    assert rec.events[-1].kind == "radio_reset" and "restarting B" in rec.events[-1].message


def test_every_step_failing_names_the_failures_in_the_error():
    import usb1

    host, rec, log = Host(2), Rec(), []
    recover = [("resetting A", recorder(log, "a", usb1.USBErrorIO())),
               ("restarting B", recorder(log, "b", OSError("pnputil missing")))]
    with pytest.raises(radio.RadioError) as caught:
        asyncio.run(radio.start_radio(host, rec, timeout=0.05, recover=recover))
    assert host.calls == 1
    assert "LIBUSB_ERROR_IO" in str(caught.value) and "pnputil missing" in str(caught.value)


def test_an_unexpected_step_error_is_not_swallowed():
    host, rec = Host(1), Rec()
    with pytest.raises(ZeroDivisionError):
        asyncio.run(radio.start_radio(host, rec, timeout=0.05, recover=[("x", lambda: 1 / 0)]))


def test_without_recovery_steps_the_first_timeout_raises():
    host, rec = Host(1), Rec()
    with pytest.raises(radio.RadioError):
        asyncio.run(radio.start_radio(host, rec, timeout=0.05))
    assert (host.calls, host.closed) == (1, 1)
    assert [e.kind for e in rec.events] == ["radio_timeout"]


def test_recovery_steps_reset_usb_first_then_restart_the_device_in_windows(monkeypatch):
    from podbridge import winusb

    calls, machine = [], object()
    monkeypatch.setattr(radio, "usb_reset", lambda vid, pid: calls.append(("usb_reset", vid, pid)))
    monkeypatch.setattr(winusb, "restart_device", lambda m: calls.append(("restart_device", m)))
    steps = radio.recovery_steps(machine, 0x0BDA, 0x887B)
    assert all(isinstance(what, str) and what for what, _ in steps)
    for _, step in steps:
        step()
    assert calls == [("usb_reset", 0x0BDA, 0x887B), ("restart_device", machine)]


class FakeHandle:
    def __init__(self, log, reset_error=None):
        self.log, self.reset_error = log, reset_error

    def claimInterface(self, number):
        self.log.append(("claim", number))

    def resetDevice(self):
        self.log.append("reset")
        if self.reset_error is not None:
            raise self.reset_error

    def close(self):
        self.log.append("close")


class FakeContext:
    def __init__(self, log, handle):
        self.log, self.handle = log, handle

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.log.append("context closed")

    def openByVendorIDAndProductID(self, vid, pid):
        self.log.append(("open", vid, pid))
        return self.handle


def patch_usb(monkeypatch, log, handle):
    import usb1
    from bumble.transport import usb

    monkeypatch.setattr(usb, "load_libusb", lambda: log.append("load"))
    monkeypatch.setattr(usb1, "USBContext", lambda: FakeContext(log, handle))


def test_usb_reset_claims_the_interface_before_resetting_and_closes(monkeypatch):
    log = []
    patch_usb(monkeypatch, log, FakeHandle(log))
    radio.usb_reset(0x0BDA, 0x887B)
    assert log == ["load", ("open", 0x0BDA, 0x887B), ("claim", 0), "reset", "close", "context closed"]


def test_usb_reset_accepts_a_device_that_re_enumerated(monkeypatch):
    import usb1

    log = []
    patch_usb(monkeypatch, log, FakeHandle(log, usb1.USBErrorNotFound()))
    radio.usb_reset(0x0BDA, 0x887B)
    assert "close" in log


def test_usb_reset_of_a_missing_device_raises_an_error_start_radio_reports(monkeypatch):
    log = []
    patch_usb(monkeypatch, log, None)
    with pytest.raises(OSError, match="0BDA:887B"):
        radio.usb_reset(0x0BDA, 0x887B)


def test_usb_open_error_is_named():
    import usb1

    class Broken(Host):
        async def start(self):
            raise usb1.USBErrorAccess()

    rec = Rec()
    with pytest.raises(radio.RadioError) as caught:
        asyncio.run(radio.start_radio(Broken(0), rec, timeout=0.05))
    assert [e.kind for e in rec.events] == ["usb_open_failed"]
    assert "LIBUSB_ERROR_ACCESS" in rec.events[0].message
    assert "podbridge restore" in rec.events[0].next_step
    assert "podbridge restore" in caught.value.next_step
