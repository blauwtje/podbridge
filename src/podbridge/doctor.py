"""`podbridge doctor`: check each stage in order, stop at the first failure (Decision 17)."""
import asyncio
import ctypes
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime

from podbridge import paths
from podbridge.audio_io import CABLE_PLAYBACK, muted_default_output

CABLE_CAPTURE = "CABLE Output"
MIC_WINDOW_S = 3.0
LOOPBACK_CHUNKS = 50
MMDEVICES = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"
DEVICE_STATE_ACTIVE = 1


class StageFailed(Exception):
    """A stage failed; the message is the one-line hint."""


@dataclass
class Context:
    args: object = None
    machine: object = None
    backend_factory: object = None
    stack: AsyncExitStack = field(default_factory=AsyncExitStack)
    vid: int = 0
    pid: int = 0
    host: object = None
    connection: object = None
    radio_timeout: float = 30.0
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)

    @property
    def address(self) -> str | None:
        from podbridge import app

        return app.saved_address()


def log_path():
    return paths.appdata_dir() / "doctor.log"


async def admin_rights(ctx: Context) -> str | None:
    """admin rights"""
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except (AttributeError, OSError) as exc:
        raise StageFailed(f"cannot read admin rights ({exc}); run on Windows") from exc
    if not is_admin:
        raise StageFailed("run podbridge as administrator (accept the UAC prompt)")
    return None


async def usb_id(ctx: Context) -> str:
    """Realtek USB ID present"""
    from podbridge import app, winusb

    try:
        ctx.vid, ctx.pid = app.find_usb_ids(ctx.args, ctx.machine)
    except winusb.DriverError as exc:
        raise StageFailed(str(exc)) from exc
    return f"{ctx.vid:04X}:{ctx.pid:04X}"


async def firmware_files(ctx: Context) -> str:
    """firmware files present"""
    from podbridge import app

    try:
        return str(app.ensure_firmware())
    except app.FirmwareError as exc:
        raise StageFailed(str(exc)) from exc


async def winusb_bind(ctx: Context) -> None:
    """WinUSB bind"""
    from podbridge import winusb

    try:
        ctx.stack.enter_context(winusb.bound(ctx.vid, ctx.pid, ctx.machine))
    except winusb.AlreadyRunning as exc:
        raise StageFailed(str(exc)) from exc
    except winusb.DriverError as exc:
        raise StageFailed(f"{exc}; run podbridge restore") from exc


async def bumble_transport(ctx: Context) -> None:
    """Bumble transport and firmware load"""
    from podbridge import radio
    from podbridge.events import ConsoleReporter
    from podbridge.host import Host

    ctx.host = Host(f"usb:{ctx.vid:04X}:{ctx.pid:04X}")
    ctx.stack.push_async_callback(ctx.host.close)
    recover = radio.recovery_steps(ctx.machine, ctx.vid, ctx.pid)
    try:
        await radio.start_radio(ctx.host, ConsoleReporter(), timeout=ctx.radio_timeout, recover=recover)
    except radio.RadioError as exc:
        raise StageFailed(f"{exc}; {exc.next_step}") from exc
    except Exception as exc:
        raise StageFailed(f"{exc}; check the firmware files and that WinUSB is bound") from exc


async def bond_present(ctx: Context) -> str:
    """bond present"""
    from podbridge.host import keys_path

    address = ctx.address
    if address is None:
        raise StageFailed("no saved AirPods address; run podbridge pair")
    try:
        keys = json.loads(keys_path().read_text())
    except (OSError, ValueError) as exc:
        raise StageFailed(f"cannot read {keys_path()}; run podbridge pair") from exc
    if not any(key.startswith(address) for namespace in keys.values() for key in namespace):
        raise StageFailed(f"no bond key for {address}; run podbridge pair")
    return address


async def codec_chosen(ctx: Context) -> str:
    """A2DP codec chosen"""
    try:
        ctx.connection = await ctx.host.connect(ctx.address)
        await ctx.host.stream(ctx.connection)
    except Exception as exc:
        raise StageFailed(f"{exc or type(exc).__name__}; put the AirPods in the case, open it and retry") from exc
    return ctx.host.codec


async def mic_frames(ctx: Context) -> str:
    """AAP mic frames per second"""
    from podbridge.aap_session import AapSession

    session = asyncio.create_task(AapSession(ctx.connection, ctx.queue).run())
    try:
        await asyncio.wait({session}, timeout=MIC_WINDOW_S)
    finally:
        session.cancel()
    if session.done() and not session.cancelled() and session.exception():
        raise StageFailed(f"AAP channel failed: {session.exception()}")
    frames = ctx.queue.qsize()
    if not frames:
        raise StageFailed("no mic frames; reseat the AirPods in your ears and retry")
    return f"{frames / MIC_WINDOW_S:.1f} frames/s"


def property_values(endpoint) -> list:
    import winreg

    with winreg.OpenKey(endpoint, "Properties") as props:
        return [winreg.EnumValue(props, i)[1] for i in range(winreg.QueryInfoKey(props)[1])]


def endpoint_names(root, index: int) -> set[str]:
    """VB-CABLE names found in one active MMDevices endpoint."""
    import winreg

    with winreg.OpenKey(root, winreg.EnumKey(root, index)) as endpoint:
        if winreg.QueryValueEx(endpoint, "DeviceState")[0] != DEVICE_STATE_ACTIVE:
            return set()
        values = [v for v in property_values(endpoint) if isinstance(v, str)]
    return {name for name in (CABLE_PLAYBACK, CABLE_CAPTURE) if any(name in v for v in values)}


def flow_names(flow: str) -> set[str]:
    import winreg

    found: set[str] = set()
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{MMDEVICES}\{flow}") as root:
        for index in range(winreg.QueryInfoKey(root)[0]):
            found |= endpoint_names(root, index)
    return found


def cable_endpoints() -> set[str]:
    """Names of the active VB-CABLE endpoints, read from the MMDevices registry key."""
    return flow_names("Render") | flow_names("Capture")


async def cable_devices(ctx: Context) -> str:
    """VB-CABLE devices present"""
    try:
        missing = {CABLE_PLAYBACK, CABLE_CAPTURE} - cable_endpoints()
    except (ImportError, OSError) as exc:
        raise StageFailed(f"cannot read the audio endpoints ({exc})") from exc
    if missing:
        raise StageFailed(
            f"missing {', '.join(sorted(missing))}; restart Windows once after setup; "
            "if it is still missing, run the podbridge setup again"
        )
    return f"{CABLE_PLAYBACK}, {CABLE_CAPTURE}"


async def loopback_while_muted(ctx: Context) -> None:
    """loopback audio while muted"""
    from podbridge.selftest import tone

    backend = ctx.backend_factory()
    ctx.stack.callback(backend.close)
    heard = False
    with muted_default_output(backend, getattr(ctx.args, "output_source", None)):
        player = asyncio.create_task(asyncio.to_thread(backend.write_playback, tone(), backend.render_name()))
        for _ in range(LOOPBACK_CHUNKS):
            heard = heard or any(await asyncio.to_thread(backend.read_loopback, 960))
        await player
    if not heard:
        raise StageFailed("loopback was silent while muted; run with --output-source <device>")


STAGES = (
    admin_rights,
    usb_id,
    firmware_files,
    winusb_bind,
    bumble_transport,
    bond_present,
    codec_chosen,
    mic_frames,
    cable_devices,
    loopback_while_muted,
)


PREFLIGHT = (admin_rights, usb_id, firmware_files, cable_devices)


def record(line: str) -> None:
    print(line)
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")


async def run_stages(stages, ctx: Context, **options) -> bool:
    """Run each stage in order, record PASS or FAIL, and stop at the first failure.

    A quiet run records only the failure. Options: quiet; report(line), default `record`."""
    quiet, report = options.get("quiet", False), options.get("report", record)
    async with ctx.stack:
        for stage in stages:
            try:
                detail = await stage(ctx)
            except StageFailed as exc:
                report(f"FAIL {stage.__doc__}: {exc}")
                return False
            except Exception as exc:
                report(f"FAIL {stage.__doc__}: {type(exc).__name__}: {exc}")
                return False
            if not quiet:
                report(f"PASS {stage.__doc__}" + (f": {detail}" if detail else ""))
    return True


def main(args=None) -> int:
    from podbridge import winusb
    from podbridge.audio_io import PyAudioWPatchBackend

    source = getattr(args, "output_source", None)
    factory = lambda: PyAudioWPatchBackend(output_source=source)
    ctx = Context(args=args, machine=winusb.Machine(), backend_factory=factory)
    return 0 if asyncio.run(run_stages(STAGES, ctx)) else 1
