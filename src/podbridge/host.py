"""One Bumble host: pairs with the AirPods, keeps the bond and streams A2DP from the encoder."""
import asyncio
import logging
import os

from bumble import a2dp, avdtp, core, hci, sdp
from bumble.device import Device
from bumble.keys import JsonKeyStore
from bumble.transport import open_transport

from podbridge import paths, rtk_download
from podbridge.a2dp_encoder import SAMPLE_RATE, SBC_SAMPLE_RATE, A2dpEncoder

log = logging.getLogger("podbridge")
APPLE_VENDOR_ID = 0x004C
AIRPODS_NAME = "AirPods"
A2DP_SOURCE_HANDLE = 0x10001
DEVICE_ID_HANDLE = 0x10002
ADTS_HEADER_SIZE = 7
SBC_BITPOOL = 53
PACKET_QUEUE = 64
log = logging.getLogger("podbridge")
_BT_SIG_SOURCE = 1
_DID_VERSION = 0x0100
DID_SPECIFICATION_ID = 0x0200
DID_VENDOR_ID = 0x0201
DID_PRODUCT_ID = 0x0202
DID_VERSION_ID = 0x0203
DID_PRIMARY_RECORD = 0x0204
DID_VENDOR_ID_SOURCE = 0x0205
SBC_ONE_FRAME_HEADER = bytes([1])


def device_id_records(handle: int) -> list[sdp.ServiceAttribute]:
    """Device ID profile record (PnP Information, 0x1200) with Apple's vendor ID."""
    element = sdp.DataElement
    return [
        sdp.ServiceAttribute(sdp.SDP_SERVICE_RECORD_HANDLE_ATTRIBUTE_ID, element.unsigned_integer_32(handle)),
        sdp.ServiceAttribute(
            sdp.SDP_BROWSE_GROUP_LIST_ATTRIBUTE_ID, element.sequence([element.uuid(sdp.SDP_PUBLIC_BROWSE_ROOT)])
        ),
        sdp.ServiceAttribute(
            sdp.SDP_SERVICE_CLASS_ID_LIST_ATTRIBUTE_ID, element.sequence([element.uuid(core.BT_PNP_INFORMATION_SERVICE)])
        ),
        sdp.ServiceAttribute(DID_SPECIFICATION_ID, element.unsigned_integer_16(0x0103)),
        sdp.ServiceAttribute(DID_VENDOR_ID, element.unsigned_integer_16(APPLE_VENDOR_ID)),
        sdp.ServiceAttribute(DID_PRODUCT_ID, element.unsigned_integer_16(0x0000)),
        sdp.ServiceAttribute(DID_VERSION_ID, element.unsigned_integer_16(_DID_VERSION)),
        sdp.ServiceAttribute(DID_PRIMARY_RECORD, element.boolean(True)),
        sdp.ServiceAttribute(DID_VENDOR_ID_SOURCE, element.unsigned_integer_16(_BT_SIG_SOURCE)),
    ]


def sdp_records() -> dict[int, list[sdp.ServiceAttribute]]:
    """Decision 10: an A2DP source record and an Apple Device ID record; no HFP or HSP."""
    return {
        A2DP_SOURCE_HANDLE: a2dp.make_audio_source_service_sdp_records(A2DP_SOURCE_HANDLE),
        DEVICE_ID_HANDLE: device_id_records(DEVICE_ID_HANDLE),
    }


def use_firmware_folder() -> str:
    """Point Bumble's Realtek driver at the firmware folder of Decision 11."""
    from podbridge import app  # app imports this module

    folder = str(app.ensure_firmware())
    os.environ["BUMBLE_RTK_FIRMWARE_DIR"] = folder
    return folder


def keys_path():
    return paths.appdata_dir() / "keys.json"


def build_device(hci_source, hci_sink, keystore_file=None) -> Device:
    """Classic-only Device with the SDP records and the bond keystore."""
    device = Device.with_hci("podbridge", hci.Address("F0:F1:F2:F3:F4:F5"), hci_source, hci_sink)
    device.classic_enabled = True
    device.le_enabled = False
    path = keystore_file or keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    device.keystore = JsonKeyStore(None, str(path))
    device.sdp_service_records = sdp_records()
    return device


def codec_capabilities(codec: str) -> avdtp.MediaCodecCapabilities:
    if codec == "aac":
        info = a2dp.AacMediaCodecInformation(
            object_type=a2dp.AacMediaCodecInformation.ObjectType.MPEG_2_AAC_LC,
            sampling_frequency=a2dp.AacMediaCodecInformation.SamplingFrequency.from_int(SAMPLE_RATE),
            channels=a2dp.AacMediaCodecInformation.Channels.STEREO,
            vbr=1,
            bitrate=256_000,
        )
        codec_type = a2dp.A2DP_MPEG_2_4_AAC_CODEC_TYPE
    else:
        sbc = a2dp.SbcMediaCodecInformation
        info = sbc(
            sampling_frequency=sbc.SamplingFrequency.from_int(SBC_SAMPLE_RATE),
            channel_mode=sbc.ChannelMode.JOINT_STEREO,
            block_length=sbc.BlockLength.BL_16,
            subbands=sbc.Subbands.S_8,
            allocation_method=sbc.AllocationMethod.LOUDNESS,
            minimum_bitpool_value=2,
            maximum_bitpool_value=SBC_BITPOOL,
        )
        codec_type = a2dp.A2DP_SBC_CODEC_TYPE
    return avdtp.MediaCodecCapabilities(avdtp.AVDTP_AUDIO_MEDIA_TYPE, codec_type, info)


class EncodedPacketSource:
    """`feed(pcm)` encodes and queues RTP packets; `packets` is the generator Bumble's pump reads."""

    def __init__(self, encoder: A2dpEncoder):
        self.encoder = encoder
        self._queue: asyncio.Queue = asyncio.Queue(PACKET_QUEUE)
        self._sequence = 0
        self._samples = 0

    def feed(self, pcm: bytes) -> None:
        """Queue the frames of one PCM block; the oldest packet is dropped when the queue is full."""
        for frame in self.encoder.encode(pcm):
            if self._queue.full():
                self._queue.get_nowait()
            self._queue.put_nowait(frame)

    def _packet(self, frame: bytes) -> avdtp.MediaPacket:
        if self.encoder.codec == "aac":
            payload = bytes(
                a2dp.AacAudioRtpPacket.for_simple_aac(self.encoder.rate, 2, frame[ADTS_HEADER_SIZE:])
            )
            samples, payload_type = 1024, 96
        else:
            payload = SBC_ONE_FRAME_HEADER + frame
            samples, payload_type = 128, 96
        seconds = self._samples / self.encoder.rate
        packet = avdtp.MediaPacket(
            2, 0, 0, 0, self._sequence, int(1000 * seconds), 0, [], payload_type, payload
        )
        packet.timestamp_seconds = seconds
        self._sequence = (self._sequence + 1) & 0xFFFF
        self._samples += samples
        return packet

    @property
    def packets(self):
        async def generate():
            while True:
                yield self._packet(await self._queue.get())

        return generate()


async def _first_connection(*attempts):
    """Run the connection attempts together and return the first that succeeds.

    The others are cancelled, and one that connected anyway is disconnected; if every
    attempt fails, the first failure is raised.
    """
    tasks = [asyncio.ensure_future(attempt) for attempt in attempts]
    errors, winner = [], None
    try:
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.exception() is None:
                    winner = task.result()
                    return winner
                errors.append(task.exception())
        raise errors[0]
    finally:
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for loser in results:
            if loser is not winner and not isinstance(loser, BaseException):
                await _disconnect(loser)


async def _disconnect(connection) -> None:
    """Drop a connection that lost the race; a failure is logged, not raised over the winner."""
    try:
        await connection.disconnect()
    except Exception:
        log.warning("could not disconnect the losing connection to %s", connection.peer_address, exc_info=True)


class Host:
    """Opens the USB controller, pairs, and streams the encoder's output to a connected peer."""

    def __init__(self, transport_name: str, codec: str = "aac"):
        self.transport_name = transport_name
        self.source = EncodedPacketSource(A2dpEncoder(codec))
        self.codec = codec
        self.device: Device | None = None
        self._transport = None

    async def start(self) -> Device:
        use_firmware_folder()
        rtk_download.install()
        self._transport = await open_transport(self.transport_name)
        self.device = build_device(self._transport.source, self._transport.sink)
        await self.device.power_on()
        return self.device

    async def pair(self, timeout: float = 60.0, address: str | None = None):
        """Pair with the AirPods (case button held) within one `timeout` and return the connection.

        Accepting a connection they start and connecting to them (at `address`, else to the
        first inquiry result named AirPods) run side by side; the first to connect wins and
        the other is cancelled.
        """
        await self.device.set_discoverable(True)
        await self.device.set_connectable(True)
        async with asyncio.timeout(timeout):
            connection = await _first_connection(self.device.accept(), self._page(address))
            await connection.authenticate()
            await connection.encrypt()
        return connection

    async def _page(self, address: str | None):
        if address is None:
            address = await self._find_airpods()
        try:
            return await self.device.connect(address, transport=core.PhysicalTransport.BR_EDR)
        except asyncio.CancelledError:
            # Bumble only cancels a page on its own timeout; stop the controller's page here.
            try:
                await self.device.cancel_connection(address)
            except Exception:
                log.warning("could not cancel the page to %s", address, exc_info=True)
            raise

    async def _find_airpods(self):
        """Run an inquiry until a device answers with an AirPods name and return its address."""
        found = asyncio.get_running_loop().create_future()

        def on_result(address, _class_of_device, data, _rssi):
            name = data.get(core.AdvertisingData.COMPLETE_LOCAL_NAME) or data.get(
                core.AdvertisingData.SHORTENED_LOCAL_NAME
            )
            if name and AIRPODS_NAME in name and not found.done():
                found.set_result(address)

        self.device.on("inquiry_result", on_result)
        try:
            await self.device.start_discovery()
            return await found
        finally:
            self.device.remove_listener("inquiry_result", on_result)
            await self.device.stop_discovery()

    async def connect(self, address: str):
        """Reconnect to a bonded peer and encrypt with the stored key."""
        connection = await self.device.connect(address, transport=core.PhysicalTransport.BR_EDR)
        await connection.authenticate()
        await connection.encrypt()
        return connection

    async def stream(self, connection) -> avdtp.Stream:
        """Open AVDTP to the peer once and start the stream; AAC first, SBC on that protocol when AAC fails."""
        version = await avdtp.find_avdtp_service_with_connection(connection)
        if version is None:
            raise RuntimeError("peer has no AVDTP service")
        # why: AVDTP allows one signaling channel per peer, so the SBC retry must reuse this protocol
        protocol = await avdtp.Protocol.connect(connection, version)
        await protocol.discover_remote_endpoints()
        if self.codec != "aac":
            return await self._start(protocol)
        try:
            return await self._start(protocol)
        except Exception as exc:
            # raised inside the handler so an SBC failure carries the AAC failure as its __context__
            log.warning("AAC stream failed (%r); trying SBC on the same AVDTP connection", exc)
            self.codec = "sbc"
            self.source = EncodedPacketSource(A2dpEncoder("sbc"))
            return await self._start(protocol)

    async def _start(self, protocol: avdtp.Protocol) -> avdtp.Stream:
        """Pick the peer's sink for our codec, configure a stream to it and start it."""
        codec_type = a2dp.A2DP_MPEG_2_4_AAC_CODEC_TYPE if self.codec == "aac" else a2dp.A2DP_SBC_CODEC_TYPE
        sink = protocol.find_remote_sink_by_codec(avdtp.AVDTP_AUDIO_MEDIA_TYPE, codec_type)
        if sink is None:
            raise RuntimeError(f"peer has no {self.codec} sink")
        pump = avdtp.MediaPacketPump(self.source.packets)
        local = protocol.add_source(codec_capabilities(self.codec), pump)
        stream = await protocol.create_stream(local, sink)
        await stream.start()
        return stream

    async def close(self) -> None:
        if self._transport is not None:
            await self._transport.close()
            self._transport = None
