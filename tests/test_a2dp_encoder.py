import io
import math
import struct

import av
import pytest

from podbridge.a2dp_encoder import A2dpEncoder


def tone(seconds: float = 0.5, rate: int = 48000) -> bytes:
    n = int(seconds * rate)
    out = bytearray()
    for i in range(n):
        v = int(12000 * math.sin(2 * math.pi * 440 * i / rate))
        out += struct.pack("<hh", v, v)
    return bytes(out)


def encode_all(codec: str) -> list[bytes]:
    enc = A2dpEncoder(codec)
    pcm = tone()
    frames = []
    step = 960 * 4
    for i in range(0, len(pcm), step):
        frames += enc.encode(pcm[i : i + step])
    return frames


def test_aac_frames_are_adts_and_decode():
    frames = encode_all("aac")
    assert len(frames) > 10
    assert all(f[0] == 0xFF and f[1] & 0xF0 == 0xF0 for f in frames)
    assert all(((f[3] & 3) << 11 | f[4] << 3 | f[5] >> 5) == len(f) for f in frames)
    with av.open(io.BytesIO(b"".join(frames)), format="aac") as c:
        s = c.streams.audio[0]
        assert (s.rate, s.channels) == (48000, 2)
        assert sum(f.samples for f in c.decode(s)) >= 1024 * 10


def test_sbc_frames_have_syncword_and_decode():
    frames = encode_all("sbc")
    assert len(frames) > 10
    assert all(f[0] == 0x9C for f in frames)
    dec = av.CodecContext.create("sbc", "r")
    dec.sample_rate, dec.layout = 44100, "stereo"
    samples = 0
    for f in frames:
        for fr in dec.decode(av.Packet(f)):
            samples += fr.samples
    assert samples > 0


def test_unknown_codec_raises():
    with pytest.raises(ValueError):
        A2dpEncoder("mp3")
