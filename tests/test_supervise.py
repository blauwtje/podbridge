import asyncio

import pytest

from podbridge.supervise import AudioGaveUp, Pipeline, supervise


class Rec:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def run(coro):
    return asyncio.run(coro)


def test_restarts_after_loss_then_finishes():
    rec, calls = Rec(), []

    async def factory():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("gone")

    run(supervise(Pipeline("mic", factory, lambda: calls.append("reopen")), rec))
    assert calls == [1, "reopen", 1]
    assert [(e.kind, e.attempt) for e in rec.events] == [("audio_lost", 0), ("audio_restarting", 1)]


def test_gives_up_after_attempts():
    rec, reopens = Rec(), []

    async def factory():
        raise RuntimeError("dead")

    with pytest.raises(AudioGaveUp):
        run(supervise(Pipeline("mic", factory, lambda: reopens.append(1)), rec, attempts=2))
    assert len(reopens) == 2
    assert rec.events[-1].kind == "audio_failed"
    assert "dead" in rec.events[-1].message


def test_failed_reopen_counts_and_other_errors_propagate():
    rec = Rec()

    async def factory():
        raise OSError("x")

    def bad():
        raise OSError("no endpoint")

    with pytest.raises(AudioGaveUp):
        run(supervise(Pipeline("mic", factory, bad), rec, attempts=1))

    async def boom():
        raise ValueError("bug")

    with pytest.raises(ValueError):
        run(supervise(Pipeline("mic", boom, lambda: None), rec))
