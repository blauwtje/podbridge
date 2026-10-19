"""Decode the AirPods mic AAC-ELD access units to 48 kHz mono int16."""

from array import array

import av

MISLABELLED_RATE_ASC = bytes.fromhex("F8E63000")
DECODE_RATE = 64000
OUT_RATE = 48000
FRAME_SAMPLES = 480
MAX_AU = 512


class MicDecoder:
    def __init__(self) -> None:
        self._ctx = av.codec.CodecContext.create(av.codec.Codec("aac", "r"))
        self._ctx.extradata = MISLABELLED_RATE_ASC
        self._ctx.sample_rate = DECODE_RATE
        self._ctx.layout = "mono"
        self._ctx.open()
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=OUT_RATE)

    def decode(self, au: bytes) -> array:
        """Return int16 samples at 48 kHz; a bad AU gives silence."""
        try:
            if not 0 < len(au) <= MAX_AU:
                raise ValueError("bad access unit size")
            frames = self._ctx.decode(av.Packet(au))
        except (av.error.FFmpegError, ValueError):
            frames = [self._silence()]
        out = array("h")
        for frame in frames:
            for resampled in self._resampler.resample(frame):
                out.frombytes(bytes(resampled.planes[0])[: resampled.samples * 2])
        return out

    def _silence(self) -> av.AudioFrame:
        frame = av.AudioFrame(format="fltp", layout="mono", samples=FRAME_SAMPLES)
        frame.sample_rate = DECODE_RATE
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        return frame
