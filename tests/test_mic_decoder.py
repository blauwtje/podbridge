import os

from podbridge.mic_decoder import MicDecoder


def total(dec, au, n=20):
    return sum(len(dec.decode(au)) for _ in range(n))


def test_valid_au_gives_48k_samples():
    assert abs(total(MicDecoder(), bytes(100)) - 7200) <= 480


def test_garbage_au_gives_silence():
    dec = MicDecoder()
    n = total(dec, os.urandom(100))
    out = dec.decode(os.urandom(100))
    assert n > 0 and not any(out)


def test_oversized_or_empty_au_gives_silence():
    dec = MicDecoder()
    assert not any(dec.decode(bytes(513))) and not any(dec.decode(b""))
