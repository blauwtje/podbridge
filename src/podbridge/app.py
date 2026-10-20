"""The `run`, `pair` and `firmware` commands: two asyncio pipelines inside the WinUSB bind (Decision 14)."""
import asyncio
import hashlib
import json
import logging
import sys
import time
import urllib.request
from array import array
from contextlib import contextmanager
from typing import Callable, NamedTuple

from podbridge import crash, paths
from podbridge.aap_session import AapSession
from podbridge.events import ConsoleReporter, Event
from podbridge.audio_io import CABLE_PLAYBACK, AudioBackend, Volume, apply_gain, muted_default_output, mute_state_path
from podbridge.host import EncodedPacketSource, Host
from podbridge.mic_decoder import MicDecoder

LOOPBACK_FRAMES = 960
RECONNECT_S = 3.0
TICK_S = 0.5
WRITE_TIMEOUT_S = 5.0
DISCONNECT_TIMEOUT_S = 5.0
RESTORE_NOTE = "restoring the driver also failed"  # the note winusb.bound adds
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
STATS_S = 10.0
SESSION_LOG = "session.log"
LOG_FORMAT = "%(asctime)s.%(msecs)03d %(name)s [%(threadName)s] %(message)s"
log = logging.getLogger("podbridge")
DISCORD_HINT = (
    "In Discord set:\n"
    '  input  "CABLE Output"\n'
    '  output "Default"\n'
    'Ignore "CABLE Input" and "CABLE In 16ch": podbridge uses them itself.'
)


class Link(NamedTuple):
    queue: asyncio.Queue
    reporter: object
    settings: object = None


class SafeReporter:
    """Forwards events and step changes to `inner`; a reporter that raises is logged and ignored."""

    def __init__(self, inner):
        self.inner = inner

    def emit(self, event) -> None:
        log.info("event %s: %s", event.kind, event.message)
        try:
            self.inner.emit(event)
        except Exception:
            log.warning("reporter failed on %s", event.kind, exc_info=True)

    def set_step(self, name, status) -> None:
        setter = getattr(self.inner, "set_step", None)
        if setter is None:
            return
        try:
            setter(name, status)
        except Exception:
            log.warning("reporter failed on step %s", name, exc_info=True)


    def set_airpods(self, data) -> None:
        self._forward("set_airpods", data)

    def set_writer(self, fn) -> None:
        self._forward("set_writer", fn)

    def attach_session(self, cancel) -> None:
        self._forward("attach_session", cancel)

    def _forward(self, name, value) -> None:
        method = getattr(self.inner, name, None)
        if method is None:
            return
        try:
            method(value)
        except Exception:
            log.warning("reporter failed on %s", name, exc_info=True)


class Session(NamedTuple):
    address: str | None = None
    reporter: object = None
    make_backend: Callable | None = None
    recover: tuple = ()  # (what, step) pairs start_radio runs when the radio does not answer
    volume: Volume | None = None  # the endpoint gain the output pipeline applies; None is full volume


def step(reporter, name, status) -> None:
    """Tell the reporter a run step changed; a console reporter has no steps."""
    log.info("step %s %s", name, status)
    setter = getattr(reporter, "set_step", None)
    if setter is not None:
        setter(name, status)


def mono_to_stereo(samples: array) -> bytes:
    """CABLE Input is opened as stereo; send the mono mic on both channels."""
    stereo = array("h")
    for sample in samples:
        stereo.extend((sample, sample))
    return stereo.tobytes()


async def output_pipeline(backend: AudioBackend, current_source: Callable[[], EncodedPacketSource],
                          volume: Volume | None = None) -> None:
    """Loopback capture to the encoder to the A2DP packet queue, until cancelled.

    `current_source` is asked per frame: the SBC fallback swaps `host.source` mid-session.
    `volume.gain` scales each chunk, since the capture ignores the Windows volume.
    A stats line every STATS_S shows where audio stops: reads, silent timeouts, peak level, fed source.
    """
    reads, peak, source, last = 0, 0, None, time.monotonic()
    while True:
        pcm = await asyncio.to_thread(backend.read_loopback, LOOPBACK_FRAMES)
        source = current_source()
        reads += 1
        if volume is not None:
            pcm = apply_gain(pcm, volume.gain)
        samples = array("h")
        samples.frombytes(pcm[: len(pcm) // 2 * 2])
        if samples:
            peak = max(peak, max(samples), -min(samples))
        try:
            source.feed(pcm)
        except Exception:
            log.exception("feed failed on %s#%s", type(source).__name__, id(source))
            raise
        now = time.monotonic()
        if now - last >= STATS_S:
            log.info(
                "output stats: frames=%d silent_reads=%d peak=%d source=%s#%d current_source_none=%s",
                reads * LOOPBACK_FRAMES, getattr(backend, "silent_timeouts", 0), peak,
                type(source).__name__, id(source), source is None,
            )
            reads, peak, last = 0, 0, now


async def mic_pipeline(queue: asyncio.Queue, decoder: MicDecoder, backend: AudioBackend) -> None:
    """AAP access units to the decoder to VB-CABLE, until cancelled."""
    units = decoded = writes = 0
    last = time.monotonic()
    while True:
        samples = decoder.decode(await queue.get())
        units += 1
        decoded += len(samples or ())
        if samples:
            await asyncio.to_thread(backend.write_playback, mono_to_stereo(samples), CABLE_PLAYBACK)
            writes += 1
        now = time.monotonic()
        if now - last >= STATS_S:
            log.info("mic stats: access_units=%d decoded_samples=%d cable_writes=%d", units, decoded, writes)
            units = decoded = writes = 0
            last = now


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


async def serve_once(host: Host, address: str, link: Link) -> None:
    from podbridge import bond

    step(link.reporter, "connecting", "active")
    connection = await bond.connect_bonded(host, address, link.reporter)
    disconnected = asyncio.get_running_loop().create_future()
    connection.on("disconnection", lambda *_: disconnected.done() or disconnected.set_result(None))
    try:
        await serve_connection(host, connection, disconnected, link)
    finally:
        step(link.reporter, "connected", "pending")
        # why: a link left up makes the next connect to this address fail with ACL_CONNECTION_ALREADY_EXISTS
        if not disconnected.done():
            await drop_link(connection)


async def drop_link(connection) -> None:
    """Disconnect an ACL we still hold; a failure is logged so the error that ended the session stays the one raised."""
    try:
        await asyncio.wait_for(connection.disconnect(), DISCONNECT_TIMEOUT_S)
    except Exception as exc:
        log.warning("could not disconnect the AirPods link: %r", exc)


async def serve_connection(host: Host, connection, disconnected: asyncio.Future, link: Link) -> None:
    """Stream A2DP, then run the AAP session until it ends or the peer drops the link."""
    from podbridge import settings

    await host.stream(connection)
    log.info("A2DP streaming with %s", getattr(host, "codec", "?"))
    step(link.reporter, "connecting", "done")
    step(link.reporter, "connected", "done")
    state = link.settings or settings.SettingsState()
    session = AapSession(
        connection, link.queue, on_event=state.apply, ca_choice=settings.load_ca_choice(), reporter=link.reporter
    )
    loop = asyncio.get_running_loop()

    def write_setting(key, value) -> None:
        frame = state.write(key, value, time.monotonic())
        if key == "conversation_awareness":
            settings.save_ca_choice(value)
            session.ca_choice = value
        session.send(frame)

    async def write(key, value) -> None:
        write_setting(key, value)

    def submit(key, value) -> None:
        """Called from the window thread: run the write on this loop and raise its error there."""
        asyncio.run_coroutine_threadsafe(write(key, value), loop).result(WRITE_TIMEOUT_S)

    set_writer = getattr(link.reporter, "set_writer", None)
    if set_writer is not None:
        set_writer(submit)
    aap = asyncio.create_task(session.run())
    watcher = asyncio.create_task(watch_settings(state, getattr(link.reporter, "set_airpods", None)))
    try:
        await asyncio.wait({aap, disconnected}, return_when=asyncio.FIRST_COMPLETED)
        if aap.done():
            aap.result()
    finally:
        if set_writer is not None:
            set_writer(None)
        aap.cancel()
        watcher.cancel()


async def watch_settings(state, publish) -> None:
    """Revert unconfirmed writes after their timeout and hand each changed AirPods view to `publish`."""
    last = None
    while True:
        state.tick(time.monotonic())
        view = state.view()
        if view != last:
            last = view
            if publish is not None:
                publish(view)
        await asyncio.sleep(TICK_S)


async def serve(host: Host, address: str, link: Link) -> None:
    """Connect, stream A2DP, run the AAP session; reconnect with a growing wait after a failure."""
    from podbridge import retry

    await retry.retry_serve(lambda: serve_once(host, address, link), link.reporter, asyncio.sleep)


async def run_session(host: Host, backend: AudioBackend, session: Session) -> None:
    """Start the radio, pair when there is no address, then run both pipelines and `serve`.

    `session.make_backend` builds a fresh backend for a lost audio device; without it a restart reuses `backend`.
    """
    from podbridge import bond, host as host_module, pairing, radio, supervise

    reporter = SafeReporter(session.reporter or ConsoleReporter())
    loop, main = asyncio.get_running_loop(), asyncio.current_task()

    def cancel() -> None:
        try:
            loop.call_soon_threadsafe(main.cancel)
        except RuntimeError:  # the session already ended and closed its loop
            pass

    reporter.attach_session(cancel)
    address, make_backend = session.address, session.make_backend
    current, tasks = [backend], []
    # why: a window close can cancel during start_radio or pairing, so every exit closes the host before the driver is restored
    try:
        step(reporter, "firmware", "active")
        step(reporter, "radio", "active")
        await radio.start_radio(host, reporter, recover=session.recover)
        step(reporter, "firmware", "done")
        step(reporter, "radio", "done")
        # why: a saved address with no link key (keys.json lost or set aside) cannot reconnect, so pair it again
        if address is None or not bond.key_saved(host_module.keys_path(), address):
            step(reporter, "pairing", "active")
            connection = await pairing.pair_with_hint(host, reporter, address=address)
            if connection is None:
                return
            address = connection.peer_address.to_string(False)
            save_address(address)
        step(reporter, "pairing", "done")
        queue: asyncio.Queue = asyncio.Queue()
        reporter.emit(Event("discord_hint", DISCORD_HINT))

        def reopen() -> None:
            if make_backend is not None:
                current.append(make_backend())

        pipelines = [
            supervise.Pipeline("output", lambda: output_pipeline(current[-1], lambda: host.source, session.volume), reopen),
            supervise.Pipeline("mic", lambda: mic_pipeline(queue, MicDecoder(), current[-1]), reopen),
        ]
        tasks.extend(asyncio.create_task(supervise.supervise(p, reporter)) for p in pipelines)
        tasks.append(asyncio.create_task(serve(host, address, Link(queue, reporter))))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        for spare in current[1:]:
            spare.close()
        await host.close()


def refuse(reporter, console: bool, message: str) -> int:
    """Print a console refusal, or send it to the reporter as a failure; exit status 1."""
    if console:
        print(f"podbridge: {message}", file=sys.stderr)
    else:
        reporter.emit(Event("failed", message, "Fix this and open podbridge again."))
    return 1


BUMBLE_LOGGERS = ("bumble.avdtp", "bumble.a2dp")


class _SessionFilter(logging.Filter):
    """Podbridge lines from INFO up; Bumble lines at any level, except the per-packet ones."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith("bumble"):
            return "RTP packet" not in record.getMessage()
        return record.levelno >= logging.INFO


@contextmanager
def session_log():
    """Log INFO lines of the "podbridge" logger to session.log, overwritten each run; never stops the run."""
    logger = log
    handler = None
    try:
        path = paths.appdata_dir() / SESSION_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, mode="w", encoding="utf-8")  # flushes after every line
        handler.setLevel(logging.DEBUG)
        handler.addFilter(_SessionFilter())
        handler.setFormatter(logging.Formatter(LOG_FORMAT, "%Y-%m-%d %H:%M:%S"))
    except OSError as exc:
        print(f"podbridge: cannot write {SESSION_LOG}: {exc}", file=sys.stderr)
    level = logger.level
    bumble_levels: dict[str, int] = {}
    if handler is not None:
        # why: with no other handler Python prints warnings to stderr; keep that once ours is attached
        if not logger.hasHandlers():
            logger.addHandler(stderr := logging.StreamHandler())
            stderr.setLevel(logging.WARNING)
        else:
            stderr = None
        logger.addHandler(handler)
        logger.setLevel(min(level or logging.INFO, logging.INFO))
        for name in BUMBLE_LOGGERS:
            bumble = logging.getLogger(name)
            bumble_levels[name] = bumble.level
            bumble.addHandler(handler)
            bumble.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        for name, bumble_level in bumble_levels.items():
            bumble = logging.getLogger(name)
            bumble.removeHandler(handler)
            bumble.setLevel(bumble_level)
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()
            if stderr is not None:
                logger.removeHandler(stderr)
            logger.setLevel(level)


def run(args, machine=None, reporter=None) -> int:
    with session_log():
        return _run(args, machine, reporter)


def _run(args, machine=None, reporter=None) -> int:
    from podbridge import doctor, firmware_watch, radio, winusb
    from podbridge.audio_io import PyAudioWPatchBackend

    machine = machine or winusb.Machine()
    console = reporter is None
    reporter = SafeReporter(ConsoleReporter() if console else reporter)
    try:
        winusb.check_single_instance(machine)
    except winusb.DriverError as exc:
        return refuse(reporter, console, str(exc))
    pending = crash.restore_pending(machine.state_path) if hasattr(machine, "state_path") else None
    if pending:
        reporter.emit(pending)
    ctx = doctor.Context(args=args, machine=machine)
    step(reporter, "checks", "active")

    def report(line: str) -> None:
        doctor.record(line)
        if not console and line.startswith("FAIL"):
            reporter.emit(Event("failed", line, "Fix this and open podbridge again."))

    if not asyncio.run(doctor.run_stages(doctor.PREFLIGHT, ctx, quiet=True, report=report)):
        return 1
    step(reporter, "checks", "done")
    vid, pid = ctx.vid, ctx.pid
    try:
        backend = PyAudioWPatchBackend(output_source=args.output_source)
    except Exception as exc:
        return refuse(reporter, console, f"cannot open the audio devices ({exc})")
    if not args.output_source and "CABLE In" in backend.render_name():
        backend.close()
        return refuse(
            reporter,
            console,
            "the Windows default speaker is a VB-CABLE device; set it to your real speakers "
            "or headphones and open podbridge again",
        )
    handler = firmware_watch.install(reporter)
    session_done = False
    try:
        step(reporter, "driver", "active")
        with winusb.bound(vid, pid, machine), muted_default_output(
            backend, args.output_source, mute_state_path(machine)
        ) as volume:
            step(reporter, "driver", "done")
            host = Host(f"usb:{vid:04X}:{pid:04X}")
            address = getattr(args, "address", None) or saved_address()
            make_backend = lambda: PyAudioWPatchBackend(output_source=args.output_source)
            recover = radio.recovery_steps(machine, vid, pid)
            asyncio.run(run_session(host, backend, Session(address, reporter, make_backend, recover, volume)))
            session_done = True
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        # Ctrl+C or the window closing: a normal end, unless the driver could not be given back.
        cause = restore_cause(exc, False)
        if cause is not None:
            return report_failure(reporter, crash.restore_failure(winusb.DriverError(cause)), exc)
    except Exception as exc:
        cause = restore_cause(exc, session_done)
        event = crash.restore_failure(winusb.DriverError(cause)) if cause is not None else crash.describe(exc)
        return report_failure(reporter, event, exc)
    finally:
        firmware_watch.remove(handler)
        backend.close()
    return 0


def restore_cause(exc, session_done: bool) -> str | None:
    """Why the driver restore failed, or None when `exc` is not a failed restore.

    `winusb.bound` adds the restore error as a note when another error is leaving; with no other error, a
    `DriverError` after the session body finished is the restore itself. A mute restore failure is neither.
    """
    from podbridge import winusb

    for note in getattr(exc, "__notes__", ()):
        if RESTORE_NOTE in note:
            return note.partition(": ")[2]
    if session_done and isinstance(exc, winusb.DriverError):
        return str(exc)
    return None


def report_failure(reporter, event, exc) -> int:
    reporter.emit(event)
    crash.write_log(exc)
    return 1


def pair(args, machine=None) -> int:
    """Start the radio as `run` does, pair (with `--address` when given) and save the address."""
    from podbridge import pairing, radio, winusb

    machine = machine or winusb.Machine()
    vid, pid = find_usb_ids(args, machine)
    reporter = SafeReporter(ConsoleReporter())
    recover = radio.recovery_steps(machine, vid, pid)

    async def pair_once() -> bool:
        host = Host(f"usb:{vid:04X}:{pid:04X}")
        try:
            await radio.start_radio(host, reporter, recover=recover)
            connection = await pairing.pair_with_hint(host, reporter, address=getattr(args, "address", None))
            if connection is None:
                return False
            save_address(connection.peer_address.to_string(False))
            return True
        finally:
            await host.close()

    session_done = False
    try:
        with winusb.bound(vid, pid, machine):
            paired = asyncio.run(pair_once())
            session_done = True
    except Exception as exc:
        cause = restore_cause(exc, session_done)
        event = crash.restore_failure(winusb.DriverError(cause)) if cause is not None else crash.describe(exc)
        return report_failure(reporter, event, exc)
    if not paired:
        return 1
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
