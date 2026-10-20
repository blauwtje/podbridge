import asyncio
import types

import bumble.core
import pytest

from podbridge import retry


class Stop(Exception):
    pass


class Rec:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def test_backoff_grows_to_cap():
    assert retry.backoff(1) == retry.RECONNECT_S
    assert retry.backoff(2) == 2 * retry.RECONNECT_S
    assert retry.backoff(50) == 30.0


def test_any_connect_error_retries_and_open_case_once():
    errors = [bumble.core.ConnectionError(1, 0, None, "x"), OSError("a"), TimeoutError(), RuntimeError("r")]
    rec, waits = Rec(), []

    async def once():
        if not errors:
            raise Stop
        raise errors.pop(0)

    async def sleep(s):
        waits.append(s)

    with pytest.raises(Stop):
        asyncio.run(retry.retry_serve(once, rec, sleep))
    kinds = [e.kind for e in rec.events]
    assert [e.attempt for e in rec.events if e.kind == "connect_retry"] == [1, 2, 3, 4]
    assert kinds.count("open_case") == 1
    assert waits == [retry.backoff(n) for n in (1, 2, 3, 4)]


def test_lasting_connect_resets_count(monkeypatch):
    clock = iter([0, 0, 0, 100, 100])
    monkeypatch.setattr(retry, "time", types.SimpleNamespace(monotonic=lambda: next(clock)))
    rec, calls = Rec(), []

    async def once():
        calls.append(1)
        if len(calls) > 2:
            raise Stop
        raise OSError("x")

    async def sleep(s):
        pass

    with pytest.raises(Stop):
        asyncio.run(retry.retry_serve(once, rec, sleep))
    assert [e.attempt for e in rec.events] == [1, 1]
