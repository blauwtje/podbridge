"""The `run`, `pair` and `firmware` commands: two asyncio pipelines inside the WinUSB bind (Decision 14)."""
import asyncio
import hashlib
import json
import logging
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
# Last linux-firmware commit whose rtl8852bu_fw.bin is EPATCH v1 ("Realtech"), the only format
# the pinned Bumble reads. Later commits ship EPATCH v2 ("RTBTCore"), which makes Firmware() raise.
FIRMWARE_COMMIT = "0061a2dde6c3ad74dd5dbfa0f645408bf846f9cd"
FIRMWARE_URL = (
    "https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/rtl_bt/{name}?id="
    + FIRMWARE_COMMIT
)
FIRMWARE_SHA256 = "f1e0427317f147a78606ede85f3f8977ed0ab85c41c6b525560e5bd5a51e5818"
FIRMWARE_MAGIC = b"Realtech"
FIRMWARE_V2_MAGIC = b"RTBTCore"
DOWNLOAD_TIMEOUT_S = 60
log = logging.getLogger("podbridge")
DISCORD_HINT = (
    "In Discord set:\n"
    '  input  "CABLE Output"\n'
    '  output "Default"\n'
    'Ignore "CABLE Input" and "CABLE In 16ch": podbridge uses them itself.'
)


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
    if not asyncio.run(doctor.run_stages(doctor.PREFLIGHT, ctx, quiet=True)):
        return 1
    vid, pid = ctx.vid, ctx.pid
    try:
        backend = PyAudioWPatchBackend(output_source=args.output_source)
    except Exception as exc:
        print(f"podbridge: cannot open the audio devices ({exc})", file=sys.stderr)
        return 1
    if not args.output_source and "CABLE In" in backend.render_name():
        backend.close()
        print(
            "podbridge: the Windows default speaker is a VB-CABLE device; set it to your real speakers "
            "or headphones and open podbridge again",
            file=sys.stderr,
        )
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


class FirmwareError(RuntimeError):
    """No usable firmware file; the message names the file, why it was rejected and what to do."""


def firmware_problem(name: str, data: bytes) -> str | None:
    """Why `data` cannot be used as firmware file `name`, or None. Every firmware path calls this."""
    if name != FIRMWARE_FILE:
        return None
    if not data.startswith(FIRMWARE_MAGIC):
        if data.startswith(FIRMWARE_V2_MAGIC):
            return "EPATCH v2 firmware, Bumble reads only v1"
        if data.lstrip()[:1] == b"<":
            return "an HTML page, not firmware"
        if len(data) < len(FIRMWARE_MAGIC):
            return f"only {len(data)} bytes, not firmware"
        return f"does not start with the EPATCH v1 signature {FIRMWARE_MAGIC.decode()!r}"
    digest = hashlib.sha256(data).hexdigest()
    if digest != FIRMWARE_SHA256:
        return f"sha256 {digest[:12]} is not the pinned {FIRMWARE_SHA256[:12]}"
    return None


def file_problem(path) -> str | None:
    try:
        return firmware_problem(path.name, path.read_bytes())
    except OSError as exc:
        return f"cannot be read ({exc})"


def set_aside(path, reason: str) -> None:
    """Rename a rejected file so it is kept, never silently deleted."""
    target = path.with_name(path.name + ".rejected")
    number = 0
    while target.exists():
        number += 1
        target = path.with_name(f"{path.name}.rejected.{number}")
    try:
        path.replace(target)
    except OSError as exc:
        log.warning("firmware %s rejected (%s) and could not be set aside: %s", path, reason, exc)
        return
    log.warning("firmware %s rejected (%s); kept as %s", path, reason, target.name)


def fetch(destination) -> Exception | None:
    """Download the pinned file next to `destination`, verify it, and move it into place."""
    url = FIRMWARE_URL.format(name=destination.name)
    try:
        data = download(url)
    except OSError as exc:
        return exc
    problem = firmware_problem(destination.name, data)
    if problem:
        return FirmwareError(f"{destination.name} from {url} was rejected: {problem}")
    partial = destination.with_name(destination.name + ".part")
    try:
        partial.write_bytes(data)
        partial.replace(destination)
    except OSError as exc:
        return exc
    return None


def ensure_firmware():
    """Folder holding a valid firmware file: AppData copy, installer copy, then the pinned download."""
    downloaded = paths.appdata_dir() / "firmware"
    problems = []
    for folder in (downloaded, paths.install_dir() / "firmware"):
        file = folder / FIRMWARE_FILE
        if not file.is_file():
            continue
        problem = file_problem(file)
        if problem is None:
            return folder
        problems.append(f"{file} is {problem}")
        set_aside(file, problem)
    try:
        downloaded.mkdir(parents=True, exist_ok=True)
        error = fetch(downloaded / FIRMWARE_FILE)
    except OSError as exc:
        error = exc
    if error is None:
        return downloaded
    problems.append(f"the download failed: {error}")
    raise FirmwareError(
        f"no usable {FIRMWARE_FILE}: {'; '.join(problems)}. "
        "Connect to the internet and open podbridge again, or reinstall podbridge"
    )


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
