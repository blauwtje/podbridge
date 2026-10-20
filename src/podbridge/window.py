"""The app window: pywebview on Edge WebView2, with the session in a worker thread."""
import json
import logging
import os
import sys
import threading
from pathlib import Path

from podbridge import paths
from podbridge.ui_state import Api

log = logging.getLogger("podbridge")
JOIN_TIMEOUT_S = 10.0

WEBVIEW2_CLIENT = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_CLIENT_USER = r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def webview2_present() -> bool:
    """True when the WebView2 runtime's `pv` value is set in HKLM (WOW6432Node) or HKCU."""
    try:
        import winreg
    except ImportError:
        return False
    for root, path in (
        (winreg.HKEY_LOCAL_MACHINE, WEBVIEW2_CLIENT),
        (winreg.HKEY_CURRENT_USER, WEBVIEW2_CLIENT_USER),
    ):
        try:
            with winreg.OpenKey(root, path) as key:
                version = winreg.QueryValueEx(key, "pv")[0]
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return True
    return False


def web_dir() -> Path:
    """The built UI: inside the PyInstaller bundle; from source, the source tree's build, else the install's."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "podbridge" / "web"
    source = Path(__file__).resolve().parent / "web"
    installed = paths.install_dir() / paths.BUNDLE_CONTENTS / "podbridge" / "web"
    if not (source / "index.html").is_file() and (installed / "index.html").is_file():
        return installed
    return source


def storage_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "podbridge" / "webview"


def open(session_fn, ui_state) -> bool:
    """Show the window until it closes; False when it could not open (caller falls back to the console).

    Closing the window stops the session through `ui_state.stop()` and waits for it, so the driver
    is restored before the process ends. A failure after the session started also stops it first.
    """
    if not webview2_present():
        return False
    worker = None
    fell_back = False
    try:
        import webview

        window = webview.create_window(
            "podbridge",
            str(web_dir() / "index.html"),
            js_api=Api(ui_state, ui_state.write_setting),
            width=420,
            height=640,
            resizable=True,
        )

        def push(snapshot):
            window.evaluate_js(f"window.__pb.emit({json.dumps(snapshot)})")

        def start():
            nonlocal worker
            ui_state.subscribe(push)
            worker = threading.Thread(target=session_fn)
            worker.start()

        storage_dir().mkdir(parents=True, exist_ok=True)
        webview.start(start, storage_path=str(storage_dir()))
    except Exception:
        log.warning("the window could not open", exc_info=True)
        fell_back = True
        return False
    finally:
        if worker is not None:
            ui_state.stop()
            # why: the console fallback starts a second session, so the first must have released the radio and driver
            worker.join(None if fell_back else JOIN_TIMEOUT_S)
    return True
