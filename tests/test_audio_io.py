import pytest

from podbridge.audio_io import CABLE_PLAYBACK, FakeBackend, muted_default_output


def test_loopback_pads_and_consumes():
    b = FakeBackend(loopback=b"\x01" * 4)
    assert b.read_loopback(2) == b"\x01" * 4 + b"\0" * 4
    assert b.read_loopback(1) == b"\0" * 4


def test_playback_defaults_to_cable():
    b = FakeBackend()
    b.write_playback(b"abcd")
    assert b.played == [(CABLE_PLAYBACK, b"abcd")]


@pytest.mark.parametrize("prior", [False, True])
def test_mute_restores_prior_state(prior):
    b = FakeBackend(muted=prior)
    with muted_default_output(b):
        assert b.muted is True
    assert b.muted is prior


def test_mute_restores_on_error():
    b = FakeBackend(muted=False)
    with pytest.raises(RuntimeError):
        with muted_default_output(b):
            raise RuntimeError
    assert b.muted is False


def test_output_source_skips_mute():
    b = FakeBackend(muted=False)
    with muted_default_output(b, "Speakers"):
        assert b.muted is False


def test_mute_restores_when_console_closes(monkeypatch):
    from podbridge import winusb

    handlers = []
    monkeypatch.setattr(winusb, "install_close_handler", lambda restore: handlers.append(restore) or object())
    monkeypatch.setattr(winusb, "remove_close_handler", lambda handler: None)
    b = FakeBackend(muted=False)
    with muted_default_output(b):
        assert b.muted is True
        assert handlers, "no console-close handler registered"
        handlers[0]()
        assert b.muted is False
