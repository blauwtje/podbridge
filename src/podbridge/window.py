"""The app window: pywebview on Edge WebView2, with the session in a worker thread."""
import json
import os
import sys
import threading
from pathlib import Path

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
    """The built UI: inside the PyInstaller bundle, or the source tree."""
    bundle = getattr(sys, "_MEIPASS", None)
    base = Path(bundle) if bundle else Path(__file__).resolve().parent
    return base / "podbridge" / "web" if bundle else base / "web"


def storage_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "podbridge" / "webview"


def open(session_fn, ui_state) -> bool:
    """Show the window until it closes; False when it could not open (caller falls back to the console)."""
    if not webview2_present():
        return False
    try:
        import webview

        window = webview.create_window(
            "podbridge", str(web_dir() / "index.html"), width=420, height=640, resizable=True
        )

        def push(snapshot):
            window.evaluate_js(f"window.__pb.emit({json.dumps(snapshot)})")

        def start():
            ui_state.subscribe(push)
            threading.Thread(target=session_fn, daemon=True).start()

        storage_dir().mkdir(parents=True, exist_ok=True)
        webview.start(start, storage_path=str(storage_dir()))
    except Exception:
        return False
    return True
