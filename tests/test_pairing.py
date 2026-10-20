import asyncio

import bumble.core
import pytest

from podbridge import pairing


class Rec:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class Host:
    def __init__(self, result):
        self.result, self.calls = result, []

    async def pair(self, timeout=60.0, address=None):
        self.calls.append(self.events_seen())
        self.address = address
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def events_seen(self):
        return list(self.rec.events)


def run(result):
    rec, host = Rec(), Host(result)
    host.rec = rec
    out = asyncio.run(pairing.pair_with_hint(host, rec))
    return out, rec, host


def test_hint_before_pair_and_connection_returned():
    out, rec, host = run("conn")
    assert out == "conn"
    assert [e.kind for e in host.calls[0]] == ["pairing_hint"]
    assert [e.kind for e in rec.events] == ["pairing_hint"]


@pytest.mark.parametrize("err", [TimeoutError(), bumble.core.ConnectionError(1, 0, None, "x")])
def test_timeout_or_bumble_error_gives_next_step(err):
    out, rec, _ = run(err)
    assert out is None
    last = rec.events[-1]
    assert last.kind == "pairing_timeout"
    assert "no AirPods connected in 60 s" in last.message
    assert "podbridge run --address <address>" in last.next_step


def test_a_known_address_is_passed_to_the_host():
    rec, host = Rec(), Host("conn")
    host.rec = rec
    asyncio.run(pairing.pair_with_hint(host, rec, address="AA:BB:CC:DD:EE:FF"))
    assert host.address == "AA:BB:CC:DD:EE:FF"
