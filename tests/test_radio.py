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


def test_timeout_resets_and_retries_once():
    host, rec, resets = Host(1), Rec(), []
    assert asyncio.run(radio.start_radio(host, rec, timeout=0.05, reset=lambda: resets.append(1))) == "dev"
    assert (host.closed, resets, [e.kind for e in rec.events]) == (1, [1], ["radio_reset"])


def test_second_timeout_raises():
    host, rec = Host(2), Rec()
    with pytest.raises(radio.RadioError):
        asyncio.run(radio.start_radio(host, rec, timeout=0.05, reset=lambda: None))
    assert [e.kind for e in rec.events] == ["radio_reset", "radio_timeout"]
    assert "unplug power or reboot" in rec.events[-1].message
    assert host.closed == 2
