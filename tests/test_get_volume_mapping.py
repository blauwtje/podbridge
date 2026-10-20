from contextlib import nullcontext

import pytest

from podbridge import audio_io
from podbridge.audio_io import PyAudioWPatchBackend

# The Realtek speaker endpoint's dB curve, steeper than linear: scalar -> GetMasterVolumeLevel() in dB.
SPEAKER_DB = {0.0: -65.25, 0.3: -22.0, 0.34: -16.34, 0.5: -9.0, 1.0: 0.0}


class SpeakerEndpoint:
    def __init__(self, scalar: float) -> None:
        self.scalar = scalar

    def GetMasterVolumeLevelScalar(self) -> float:
        return self.scalar

    def GetMasterVolumeLevel(self) -> float:
        return SPEAKER_DB[self.scalar]


def gain_at(monkeypatch, scalar: float) -> float:
    monkeypatch.setattr(audio_io, "com_initialised", nullcontext)
    backend = object.__new__(PyAudioWPatchBackend)
    backend._volume = lambda: SpeakerEndpoint(scalar)
    return backend.get_volume()


@pytest.mark.parametrize("scalar", [0.3, 0.34, 0.5, 1.0])
def test_the_gain_is_linear_in_the_slider_not_the_speakers_db_curve(monkeypatch, scalar):
    assert gain_at(monkeypatch, scalar) == pytest.approx(scalar)


def test_the_gain_is_zero_at_slider_zero(monkeypatch):
    assert gain_at(monkeypatch, 0.0) == 0.0
