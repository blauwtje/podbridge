import asyncio
import logging
import time
from array import array

import pytest

from podbridge import app, audio_io
from podbridge.audio_io import FakeBackend, Volume, apply_gain, muted_default_output


def pcm(values) -> bytes:
    return array("h", values).tobytes()


def samples(raw: bytes) -> list[int]:
    out = array("h")
    out.frombytes(raw)
    return list(out)


def until(condition, timeout=2.0) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


def test_gain_half_halves_the_samples():
    assert samples(apply_gain(pcm([1000, -1000, 8, -8]), 0.5)) == [500, -500, 4, -4]


def test_gain_zero_silences():
    assert apply_gain(pcm([1000, -1000, 32767]), 0.0) == bytes(6)


def test_gain_one_leaves_the_bytes_unchanged():
    raw = pcm([1000, -1000, 32767, -32768])
    assert apply_gain(raw, 1.0) == raw


def test_gain_above_one_clips_to_int16():
    assert samples(apply_gain(pcm([20000, -20000, 100]), 2.0)) == [32767, -32768, 200]


def test_output_pipeline_applies_the_volume_gain():
    fed = []

    class Source:
        def feed(self, data):
            fed.append(data)

    async def go():
        volume = Volume()
        volume.gain = 0.5
        task = asyncio.create_task(
            app.output_pipeline(FakeBackend(loopback=pcm([1000, -1000] * 960)), lambda: Source(), volume)
        )
        while not fed:
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(go())
    assert samples(fed[0])[:4] == [500, -500, 500, -500]


def test_output_pipeline_without_a_volume_feeds_the_capture_unchanged():
    fed = []

    class Source:
        def feed(self, data):
            fed.append(data)

    async def go():
        task = asyncio.create_task(app.output_pipeline(FakeBackend(loopback=pcm([1000, -1000] * 960)), lambda: Source()))
        while not fed:
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(go())
    assert fed[0] == pcm([1000, -1000] * 960)


def test_fake_backend_volume_defaults_to_full():
    assert FakeBackend().get_volume() == 1.0


def test_watcher_mutes_again_and_follows_the_volume(caplog):
    b = FakeBackend(muted=False)
    with caplog.at_level(logging.INFO, logger="podbridge"):
        with muted_default_output(b) as volume:
            assert volume.gain == 1.0
            b.volume = 0.25
            b.muted = False  # the slider moved: Windows unmutes the endpoint
            assert until(lambda: volume.gain == 0.25 and b.muted is True)
    assert "mute: endpoint unmuted (volume 0.25); muted again" in caplog.text
    assert "volume: gain 0.250" in caplog.text


def test_watcher_stops_before_restore_so_the_prior_state_wins():
    b = FakeBackend(muted=False)
    with muted_default_output(b) as volume:
        assert until(lambda: b.muted is True)
    assert b.muted is False
    time.sleep(0.35)
    assert b.muted is False, "the watcher muted the endpoint again after restore"


def test_watcher_survives_errors_and_logs_each_kind_once(caplog):
    class Flaky(FakeBackend):
        calls = 0

        def get_volume(self):
            self.calls += 1
            if self.calls <= 4:
                raise OSError("endpoint busy")
            return self.volume

    b = Flaky()
    with caplog.at_level(logging.INFO, logger="podbridge"):
        with muted_default_output(b) as volume:
            b.volume = 0.5
            assert until(lambda: volume.gain == 0.5)
    failures = [r for r in caplog.records if r.exc_info and "endpoint busy" in str(r.exc_info[1])]
    assert len(failures) == 1


def test_output_source_does_no_watching_and_the_gain_stays_full():
    class Spy(FakeBackend):
        reads = 0

        def get_volume(self):
            self.reads += 1
            return 0.1

        def get_mute(self):
            self.reads += 1
            return super().get_mute()

    b = Spy(muted=False)
    with muted_default_output(b, "Speakers") as volume:
        time.sleep(0.35)
        assert volume.gain == 1.0
    assert b.reads == 0 and b.muted is False


def test_a_volume_notification_wakes_the_watcher_before_the_poll(monkeypatch):
    monkeypatch.setattr(audio_io, "VOLUME_POLL_S", 5.0)
    b = FakeBackend(muted=False)
    with muted_default_output(b) as volume:
        assert until(lambda: b.muted is True)
        time.sleep(0.1)  # the watcher is now waiting out its 5 s poll
        b.muted = False
        b.volume = 0.25
        b.fire_volume_change()
        assert until(lambda: b.muted is True and volume.gain == 0.25, timeout=0.5)


def test_the_volume_notification_is_unregistered_before_the_prior_mute_is_restored():
    seen = []

    class Spy(FakeBackend):
        def set_mute(self, muted):
            if not muted:
                seen.append(self.watching)
            super().set_mute(muted)

    b = Spy(muted=False)
    with muted_default_output(b):
        assert b.watching == 1
    assert seen == [0]
    assert b.watching == 0


def test_a_failed_notification_registration_falls_back_to_polling_with_one_warning(caplog, monkeypatch):
    monkeypatch.setattr(audio_io, "VOLUME_POLL_S", 0.02)

    class NoCallback(FakeBackend):
        def watch_volume(self, wake):
            raise OSError("no callback")

    b = NoCallback(muted=False)
    with caplog.at_level(logging.INFO, logger="podbridge"):
        with muted_default_output(b) as volume:
            b.volume = 0.5
            b.muted = False
            assert until(lambda: volume.gain == 0.5 and b.muted is True)
            time.sleep(0.1)
    warnings_ = [r for r in caplog.records if r.levelno == logging.WARNING and "no callback" in str(r.exc_info)]
    assert len(warnings_) == 1


def test_gain_ramps_linearly_from_the_previous_gain_across_the_chunk():
    frames = 960
    out = samples(apply_gain(pcm([32767] * 2 * frames), 0.0, 1.0))
    left, right = out[0::2], out[1::2]
    assert left == right
    assert left[0] >= 32767 * 0.99 and left[-1] == 0
    assert all(a >= b for a, b in zip(left, left[1:]))
    increment = 32767 / frames
    assert max(a - b for a, b in zip(left, left[1:])) <= increment + 1
    assert 32767 - left[0] <= increment + 1


def test_a_constant_gain_is_a_plain_scale_and_gain_one_keeps_the_bytes():
    raw = pcm([1000, -1000, 800, -800])
    assert samples(apply_gain(raw, 0.5, 0.5)) == [500, -500, 400, -400]
    assert apply_gain(raw, 1.0, 1.0) is raw


def test_output_pipeline_ramps_across_a_gain_change_between_chunks():
    fed = []
    volume = Volume()

    class Source:
        def feed(self, data):
            fed.append(data)
            volume.gain = 0.0  # the slider drops to zero after the first chunk

    async def go():
        loud = pcm([32767] * 2 * app.LOOPBACK_FRAMES)
        task = asyncio.create_task(app.output_pipeline(FakeBackend(loopback=loud * 2), lambda: Source(), volume))
        while len(fed) < 2:
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(go())
    assert samples(fed[0])[0] == 32767
    second = samples(fed[1])[0::2]
    assert second[0] >= 32767 * 0.99 and second[-1] == 0
