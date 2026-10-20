"""Encode 48 kHz stereo PCM blocks to ADTS AAC-LC or SBC frames with PyAV."""
import av

SAMPLE_RATE = 48000
SBC_SAMPLE_RATE = 44100
AAC_BIT_RATE = 256_000
SBC_BIT_RATE = 328_000
MIN_AAC_BIT_RATE = 64_000
AAC_FRAME_SAMPLES = 1024
# Bytes around one AAC frame in an RTP packet: RTP header (12) plus LATM length and StreamMuxConfig (about 16 measured).
AAC_PACKET_OVERHEAD = 32
# Largest frame over its mean size seen from the VBR encoder: 1.21 to 1.28 on test noise, 1.79 in a real run, with margin.
AAC_VBR_PEAK = 1.9
_ADTS_RATE_INDEX = {96000: 0, 88200: 1, 64000: 2, 48000: 3, 44100: 4, 32000: 5}


def _adts_header(length: int, rate: int) -> bytes:
    """7-byte ADTS header for an AAC-LC stereo frame, no CRC."""
    n = length + 7
    return bytes([
        0xFF, 0xF1,
        (1 << 6) | (_ADTS_RATE_INDEX[rate] << 2),
        (2 << 6) | (n >> 11),
        (n >> 3) & 0xFF,
        ((n & 7) << 5) | 0x1F,
        0xFC,
    ])


def aac_bit_rate_for_mtu(peer_mtu: int, rate: int = SAMPLE_RATE) -> int:
    """Highest AAC bit rate whose VBR peak frame, with its RTP and LATM bytes, still fits one `peer_mtu` packet.

    Like Android's a2dp_aac_encoder, which caps the bit rate from the peer MTU: Bumble sends each RTP packet as one
    L2CAP SDU and a sink may drop an SDU above its MTU.
    """
    frame_bytes = (peer_mtu - AAC_PACKET_OVERHEAD) / AAC_VBR_PEAK
    bit_rate = int(frame_bytes * 8 * rate / AAC_FRAME_SAMPLES)
    return max(MIN_AAC_BIT_RATE, min(AAC_BIT_RATE, bit_rate))


class A2dpEncoder:
    """`encode(pcm)` takes int16 interleaved stereo bytes at 48 kHz and returns a list of frames."""

    def __init__(self, codec: str = "aac", bit_rate: int | None = None):
        if codec not in ("aac", "sbc"):
            raise ValueError(f"unknown codec {codec!r}")
        self.codec = codec
        self.rate = SAMPLE_RATE if codec == "aac" else SBC_SAMPLE_RATE
        fmt = "fltp" if codec == "aac" else "s16"
        self._resampler = av.AudioResampler(format=fmt, layout="stereo", rate=self.rate)
        self._enc = av.CodecContext.create(codec, "w")
        self._enc.sample_rate = self.rate
        self._enc.layout = "stereo"
        self._enc.format = fmt
        self.bit_rate = bit_rate or (AAC_BIT_RATE if codec == "aac" else SBC_BIT_RATE)
        self._enc.bit_rate = self.bit_rate
        self._enc.open()
        self._fifo = av.AudioFifo()
        self._pts = 0

    def encode(self, pcm: bytes) -> list[bytes]:
        frame = av.AudioFrame(format="s16", layout="stereo", samples=len(pcm) // 4)
        frame.planes[0].update(pcm)
        frame.sample_rate = SAMPLE_RATE
        for out in self._resampler.resample(frame):
            self._fifo.write(out)
        size = self._enc.frame_size
        frames = []
        while (chunk := self._fifo.read(size)) is not None:
            chunk.pts = self._pts
            self._pts += chunk.samples
            frames.extend(self._packets(chunk))
        return frames

    def _packets(self, chunk) -> list[bytes]:
        packets = [bytes(p) for p in self._enc.encode(chunk)]
        if self.codec == "aac":
            return [_adts_header(len(p), self.rate) + p for p in packets]
        return packets
