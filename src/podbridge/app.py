"""The `run`, `pair` and `firmware` commands: two asyncio pipelines inside the WinUSB bind (Decision 14)."""
import asyncio
import json
import sys
import urllib.request
from array import array
from typing import Callable

from podbridge import paths
from podbridge.a2dp_encoder import A2dpEncoder
from podbridge.aap_session import AapSession
from podbridge.audio_io import CABLE_PLAYBACK, AudioBackend, muted_default_output
from podbridge.host import EncodedPacketSource, Host
from podbridge.mic_decoder import MicDecoder

LOOPBACK_FRAMES = 960
RECONNECT_S = 3.0
USB_CANDIDATES = ((0x13D3, 0x3571), (0x0BDA, 0x887B), (0x0CB8, 0xC559))
FIRMWARE_BASE = "rtl8852bu"
FIRMWARE_FILE = f"{FIRMWARE_BASE}_fw.bin"
FIRMWARE_CONFIG = f"{FIRMWARE_BASE}_config.bin"
FIRMWARE_URL = "https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/rtl_bt"
DOWNLOAD_TIMEOUT_S = 60
DISCORD_HINT = 'Discord: input "CABLE Output", output "Default"'


def mono_to_stereo(samples: array) -> bytes:
    """CABLE Input is opened as stereo; send the mono mic on both channels."""
    stereo = array("h")
    for sample in samples:
        stereo.extend((sample, sample))
    return stereo.tobytes()


async def output_pipeline(backend: AudioBackend, current_source: Callable[[], EncodedPacketSource]) -> None:
    """Loopback capture to the encoder to the A2DP packet queue, until cancelled.

    `current_source` is asked per frame: the SBC fallback swaps `host.source` mid-session.
    """
    while True:
        pcm = await asyncio.to_thread(backend.read_loopback, LOOPBACK_FRAMES)
        current_source().feed(pcm)


async def mic_pipeline(queue: asyncio.Queue, decoder: MicDecoder, backend: AudioBackend) -> None:
    """AAP access units to the decoder to VB-CABLE, until cancelled."""
    while True:
        samples = decoder.decode(await queue.get())
        if samples:
            await asyncio.to_thread(backend.write_playback, mono_to_stereo(samples), CABLE_PLAYBACK)


def device_file():
    return paths.appdata_dir() / "device.json"


def saved_address() -> str | None:
    try:
        return json.loads(device_file().read_text())["address"]
    except (OSError, ValueError, KeyError):
        return None


def save_address(address: str) -> None:
    device_file().parent.mkdir(parents=True, exist_ok=True)
    device_file().write_text(json.dumps({"address": address}))


def parse_transport(name: str) -> tuple[int, int]:
    scheme, vid, pid = name.split(":")
    if scheme != "usb":
        raise ValueError(f"--transport must look like usb:<vid>:<pid>, got {name!r}")
    return int(vid, 16), int(pid, 16)


def find_usb_ids(args, machine) -> tuple[int, int]:
    """The `--transport` ids, else the first known 8852BE ID that is connected."""
    from podbridge import winusb

    if getattr(args, "transport", None):
        return parse_transport(args.transport)
    for vid, pid in USB_CANDIDATES:
        try:
            winusb.find_instance(machine, vid, pid)
        except winusb.DriverError:
            continue
        return vid, pid
    raise winusb.DriverError("no Realtek 8852BE Bluetooth USB device found; pass --transport usb:<vid>:<pid>")


async def start_stream(host: Host, connection):
    """AAC first; one retry with SBC when the AirPods reject the AAC configuration."""
    try:
        return await host.stream(connection)
    except Exception:
        if host.codec != "aac":
            raise
    host.codec = "sbc"
    host.source = EncodedPacketSource(A2dpEncoder("sbc"))
    return await host.stream(connection)


async def serve_once(host: Host, address: str, queue: asyncio.Queue) -> None:
    connection = await host.connect(address)
    disconnected = asyncio.get_running_loop().create_future()
    connection.on("disconnection", lambda *_: disconnected.done() or disconnected.set_result(None))
    await start_stream(host, connection)
    aap = asyncio.create_task(AapSession(connection, queue).run())
    try:
        await asyncio.wait({aap, disconnected}, return_when=asyncio.FIRST_COMPLETED)
        if aap.done():
            aap.result()
    finally:
        aap.cancel()


async def serve(host: Host, address: str, queue: asyncio.Queue) -> None:
    """Connect, stream A2DP, run the AAP session; reconnect in a loop after a disconnect."""
    while True:
        try:
            await serve_once(host, address, queue)
        except (ConnectionError, OSError, TimeoutError, RuntimeError) as exc:
            print(f"podbridge: {exc}; retrying", file=sys.stderr)
        await asyncio.sleep(RECONNECT_S)


async def run_session(host: Host, backend: AudioBackend, address: str | None) -> None:
    await host.start()
    if address is None:
        print("Hold the AirPods case button until they pair.")
        connection = await host.pair()
        address = connection.peer_address.to_string(False)
        save_address(address)
    queue: asyncio.Queue = asyncio.Queue()
    print(DISCORD_HINT)
    pipelines = [
        asyncio.create_task(output_pipeline(backend, lambda: host.source)),
        asyncio.create_task(mic_pipeline(queue, MicDecoder(), backend)),
    ]
    try:
        await serve(host, address, queue)
    finally:
        for task in pipelines:
            task.cancel()
        await host.close()


def run(args, machine=None) -> int:
    from podbridge import doctor, winusb
    from podbridge.audio_io import PyAudioWPatchBackend

    machine = machine or winusb.Machine()
    ctx = doctor.Context(args=args, machine=machine)
    if not asyncio.run(doctor.run_stages(doctor.PREFLIGHT, ctx)):
        return 1
    vid, pid = ctx.vid, ctx.pid
    try:
        backend = PyAudioWPatchBackend(output_source=args.output_source)
    except Exception as exc:
        print(f"podbridge: cannot open the audio devices ({exc})", file=sys.stderr)
        return 1
    try:
        with winusb.bound(vid, pid, machine), muted_default_output(backend, args.output_source):
            host = Host(f"usb:{vid:04X}:{pid:04X}")
            asyncio.run(run_session(host, backend, getattr(args, "address", None) or saved_address()))
    except KeyboardInterrupt:
        pass
    finally:
        backend.close()
    return 0


def pair(args, machine=None) -> int:
    from podbridge import winusb

    machine = machine or winusb.Machine()
    vid, pid = find_usb_ids(args, machine)

    async def pair_once() -> None:
        host = Host(f"usb:{vid:04X}:{pid:04X}")
        await host.start()
        try:
            print("Hold the AirPods case button until they pair.")
            connection = await host.pair()
            save_address(connection.peer_address.to_string(False))
        finally:
            await host.close()

    with winusb.bound(vid, pid, machine):
        asyncio.run(pair_once())
    print(f"Paired; address saved to {device_file()}")
    return 0


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "podbridge"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
        return response.read()


def fetch(destination) -> OSError | None:
    try:
        destination.write_bytes(download(f"{FIRMWARE_URL}/{destination.name}"))
    except OSError as exc:
        return exc
    return None


def firmware(args) -> int:
    """Download the Realtek firmware to %APPDATA%\\podbridge\\firmware (Decision 11).

    Bumble's own downloader cannot be imported from a package install, so this fetches
    the same files from the same linux-firmware URL. The config file is optional.
    """
    target = paths.appdata_dir() / "firmware"
    target.mkdir(parents=True, exist_ok=True)
    for name, required in ((FIRMWARE_FILE, True), (FIRMWARE_CONFIG, False)):
        error = fetch(target / name)
        if error and required:
            print(f"podbridge firmware: {name} failed: {error}", file=sys.stderr)
            return 1
        if error:
            print(f"No optional {name}: {error}")
    print(f"Firmware saved to {target}")
    return 0
